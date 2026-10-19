"""Bounded trusted composition inside incumbent camera ownership, never a loader.

The managed application supplies fixed graph/worker/recording capabilities. Owner
commands choose only start/stop/status/results for this one frozen configuration.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
from pathlib import Path
import queue
import re
import subprocess
import threading
import time

if __package__:
    from .archive import RecorderRPC, canonical, digest
else:
    from archive import RecorderRPC, canonical, digest

ROOT = Path(__file__).resolve().parents[2]
CONTROL_PREFIX = "/api/home-live-session/v1/"


class SupervisorRefusal(ValueError):
    pass


def describe_worker(node_runtime, config):
    result = subprocess.run([node_runtime, "--max-old-space-size=384", str(ROOT / "tools/den-live-runtime/worker-runner.cjs")],
        cwd=ROOT, input=canonical({"op": "describe", "config": config}) + "\n", capture_output=True, text=True,
        timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode or len(result.stdout.encode()) > 65536:
        raise SupervisorRefusal("worker_description_unavailable")
    value = json.loads(result.stdout)
    if not value.get("ok"):
        raise SupervisorRefusal("worker_description_unavailable")
    return value["value"]


def compose_supervisor(config, *, graph_factory, preflight, stop_preparation=None, release=None,
                       after_source_step=None, fixed_period=False, command_factory=None):
    """Fixed managed root supplies acquisition/preflight; config never selects code.

    This creates no graph, socket or worker until an independently authorized
    operator start. Model/camera capabilities remain with the incumbent runtime.
    """
    if __package__:
        from .archive import Recorder
    else:
        from archive import Recorder
    keys = {"schema", "version", "session", "worker_config", "node_runtime", "archive_file", "archive_limits", "source_bundle_digest"}
    successor = isinstance(config, dict) and config.get("schema") == "home-live-runtime-config-2" and type(config.get("version")) is int and config['version'] == 2
    assessed = isinstance(config, dict) and config.get("schema") == "home-live-runtime-config-3" and type(config.get("version")) is int and config['version'] == 3
    expected = 3 if assessed else 2 if successor else 1
    if (not isinstance(config, dict) or set(config) not in (keys, keys | {"assessment_file"})
            or config["schema"] != "home-live-runtime-config-" + str(expected) or config["version"] != expected
            or config.get('session', {}).get('schema') != 'home-live-session-config-' + str(expected)
            or type(config.get('session', {}).get('version')) is not int or config['session']['version'] != expected
            or not Path(config["node_runtime"]).is_absolute() or not Path(config["archive_file"]).is_absolute()
            or not isinstance(config["archive_limits"], dict) or set(config["archive_limits"]) != {"max_records", "max_bytes", "min_free_bytes"}
            or not isinstance(config["source_bundle_digest"], str) or len(config["source_bundle_digest"]) != 64):
        raise SupervisorRefusal("runtime_composition_config_refused")
    worker_config = json.loads(canonical(config["worker_config"]))
    if worker_config is None and config["session"]["mode"] != "development":
        raise SupervisorRefusal("worker_configuration_required")
    archive_config = {k: v for k, v in worker_config.items() if k not in {"token", "token_file", "service_url"}} if worker_config is not None else None

    def recorder_factory(graph):
        description = describe_worker(config["node_runtime"], archive_config) if archive_config is not None else None
        manifest = {"schema": "home-live-session-manifest-1", "version": 1,
                    "session_ref": config["session"]["session_ref"], "configuration_digest": graph.configuration_digest,
                    "source_bundle_digest": config["source_bundle_digest"], "worker_config": archive_config,
                    "worker_description": description}
        if assessed:
            manifest.update(schema='home-live-session-manifest-2', version=2,
                            runtime_profile='home-live-runtime-config-3')
        return Recorder(config["archive_file"], manifest, **config["archive_limits"])

    def worker_factory(graph, recorder_port):
        if config["session"]["mode"] == "development":
            return None
        bound = {**worker_config, "service_url": "http://127.0.0.1:" + str(graph.address[1])}
        return NodeWorkerDriver(node_runtime=config["node_runtime"], config=bound, recorder=recorder_port)

    return SessionSupervisor(config["session"], graph_factory=graph_factory, recorder_factory=recorder_factory,
                             worker_factory=worker_factory, preflight=preflight, stop_preparation=stop_preparation, release=release,
                             assessment_file=config.get("assessment_file"), after_source_step=after_source_step,
                             fixed_period=fixed_period, command_factory=command_factory)


class NodeWorkerDriver:
    def __init__(self, *, node_runtime, config, recorder, controlled_time=None):
        if not isinstance(node_runtime, str) or not Path(node_runtime).is_absolute():
            raise SupervisorRefusal("explicit_node_runtime_required")
        self.node_runtime = node_runtime
        self.lock, self.lines = threading.Lock(), queue.Queue(maxsize=32)
        self.process = subprocess.Popen([node_runtime, "--max-old-space-size=384", str(ROOT / "tools/den-live-runtime/worker-runner.cjs")],
            cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        def read():
            for line in self.process.stdout:
                if len(line.encode()) > 16384:
                    break
                try:
                    self.lines.put(line, timeout=1)
                except queue.Full:
                    break
            self.lines.put(None)
        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()
        try:
            self.ready_receipt = self._command("start", config=config, recorder=recorder,
                **({"controlled_time": controlled_time} if controlled_time is not None else {}))
        except BaseException:
            self.close()
            raise

    def _command(self, op, **fields):
        with self.lock:
            try:
                self.process.stdin.write(canonical({"op": op, **fields}) + "\n")
                self.process.stdin.flush()
                line = self.lines.get(timeout=12)
                if line is None:
                    raise SupervisorRefusal("worker_process_unavailable")
                response = json.loads(line)
                if not response.get("ok"):
                    raise SupervisorRefusal("worker_command_unconfirmed")
                return response["value"]
            except (OSError, ValueError, queue.Empty):
                raise SupervisorRefusal("worker_command_unconfirmed") from None

    def readiness(self):
        return dict(self.ready_receipt)

    def status(self):
        return self._command("status")

    def drain(self, *, deadline_ms, through_cursor=0):
        return self._command("drain", deadline_ms=deadline_ms, through_cursor=through_cursor)

    def cycle(self, t):
        return self._command("tick", t=t)

    def stop_admission(self):
        """Request termination of this owned JS process without its pipe lock.

        This does not cancel a journal transaction or prove OS completion. The
        supervisor must report unresolved cleanup until independently confirmed.
        """
        if self.process.poll() is None:
            self.process.terminate()

    def close(self):
        if self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            self.process.wait(3)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(2)
        if self.process.stdout:
            self.process.stdout.close()
        self.reader.join(2)


class SessionSupervisor:
    def __init__(self, config, *, graph_factory, recorder_factory, worker_factory, preflight,
                 clock=time.monotonic, sleep=time.sleep, stop_preparation=None, release=None, assessment_file=None,
                 after_source_step=None, fixed_period=False, command_factory=None):
        keys = {"schema", "version", "session_ref", "configuration_digest", "mode", "max_runtime_s", "max_cycles", "interval_s", "drain_timeout_s"}
        # Profile3 permits the trusted command capability with finite polling.
        # Its protected Lab composition validates the literal installed factory;
        # no session JSON selects code. None remains valid for inert validation.
        assessed = isinstance(config, dict) and config.get('schema') == 'home-live-session-config-3' and type(config.get('version')) is int and config['version'] == 3
        successor = assessed or isinstance(config, dict) and config.get('schema') == 'home-live-session-config-2' and type(config.get('version')) is int and config['version'] == 2
        expected = 3 if assessed else 2 if successor else 1
        if successor:
            keys = keys - {'max_cycles'} | {'max_poll_attempts', 'max_capture_originals'}
        cycle_key = 'max_poll_attempts' if successor else 'max_cycles'
        if (not isinstance(config, dict) or set(config) != keys
                or config["schema"] != "home-live-session-config-" + str(expected) or config["version"] != expected
                or config["mode"] not in {"controlled", "development", "qualified", "live_shadow_unqualified"}
                or not isinstance(config["session_ref"], dict) or set(config["session_ref"]) != {"site_id", "room_id", "session_id", "domain_epoch"}
                or not isinstance(config["configuration_digest"], str) or not re.fullmatch(r"[a-f0-9]{64}", config["configuration_digest"])
                or any(not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}", v) for v in config["session_ref"].values())
                or any(type(config[k]) not in {int, float} or not math.isfinite(config[k]) for k in ("max_runtime_s", "interval_s", "drain_timeout_s"))
                or not 0 < config["max_runtime_s"] <= 120 or type(config[cycle_key]) is not int or not 1 <= config[cycle_key] <= (240 if successor else 24)
                or successor and (type(config['max_capture_originals']) is not int or not 1 <= config['max_capture_originals'] <= 24
                                  or not assessed and command_factory is not None)
                or not .5 <= config["interval_s"] <= 120 or not 0 < config["drain_timeout_s"] <= 10
                or not all(callable(p) for p in (graph_factory, recorder_factory, worker_factory, preflight, clock, sleep))
                or stop_preparation is not None and not callable(stop_preparation) or release is not None and not callable(release)
                or assessment_file is not None and (not isinstance(assessment_file, str) or not Path(assessment_file).is_absolute())
                or type(fixed_period) is not bool
                or command_factory is not None and (not callable(command_factory) or config["mode"] == "development")
                or after_source_step is not None and (not callable(after_source_step) or config["mode"] != "controlled")
                or fixed_period and (after_source_step is None or config["mode"] != "controlled" or config["interval_s"] < 1)):
            raise SupervisorRefusal("explicit_bounded_supervisor_configuration_required")
        self.config = json.loads(canonical(config))
        self.poll_budget, self._cycle_limit = successor, config[cycle_key]
        self.graph_factory, self.recorder_factory, self.worker_factory = graph_factory, recorder_factory, worker_factory
        self.preflight, self.clock, self.sleep = preflight, clock, sleep
        self.stop_preparation, self.release = stop_preparation, release
        # Managed controlled composition may await the existing worker's durable
        # checkpoint. It never ticks the worker or changes evidence/lease clocks.
        # Legacy acquisition retains its original post-step interval by default.
        self.after_source_step, self.fixed_period = after_source_step, fixed_period
        # Trusted installed capability, never selected by session/config JSON.
        self.command_factory, self.command_owner = command_factory, None
        self._production_command = False
        self._command_problem, self._command_drain = False, None
        self._command_ready, self._command_watchdog = False, None
        self._command_setup_done = threading.Event()
        if command_factory is None:
            self._command_setup_done.set()
        self.assessment_file, self.assessment = assessment_file, {"status": "not_required"}
        self.lock, self.stop_lock, self.cancel = threading.RLock(), threading.Lock(), threading.Event()
        self.graph = self.recorder = self.rpc = self.worker = self.thread = None
        self.state, self.reason, self.cycles, self.result = "idle", None, 0, None
        self.commands = {}
        self._recording_cache = None
        self._control_retirement_thread = None
        self._control_retirement_pending = False
        self._terminal_status = None

    def start(self):
        with self.lock:
            if self.state != "idle":
                raise SupervisorRefusal("session_already_started")
            self.state = "starting"
            if self.command_factory is not None:
                # Bound the whole optional preparation, including synchronous
                # factory/start and a late Node constructor, not only polling.
                self._command_watchdog = threading.Timer(10, self._command_preparation_timeout)
                self._command_watchdog.daemon = True
                self._command_watchdog.start()
            self.thread = threading.Thread(target=self._prepare, name="den-live-session", daemon=True)
            self.thread.start()
            return self.status()

    def _prepare(self):
        try:
            outcome = self.preflight(self.config)
            if not isinstance(outcome, dict) or outcome.get("status") != "ready" or self.config["mode"] == "qualified" and outcome.get("qualification_current") is not True:
                self.state, self.reason = "refused", "session_preflight_unmet"
                return
            if self.cancel.is_set():
                self.state, self.reason = "incomplete", "preparation_cancelled"
                return
            self.graph = self.graph_factory()
            if self.cancel.is_set():
                self.stop(reason="preparation_cancelled")
                return
            ready = self.graph.readiness()
            if ready["session_ref"] != self.config["session_ref"] or ready["configuration_digest"] != self.config["configuration_digest"] or ready["inflight"]:
                raise SupervisorRefusal("graph_binding_mismatch")
            self.recorder = self.recorder_factory(self.graph)
            self._recording_cache = self.recorder.status()
            if self._recording_cache["status"] != "ready":
                raise SupervisorRefusal("recorder_not_ready")
            routing = self.recorder
            if self.command_factory is not None:
                if __package__:
                    from .room_command_session import CanonicalCommandSession
                    from .room_production_command_factory import ProductionCommandFactory, ProductionCommandSession
                else:
                    from room_command_session import CanonicalCommandSession
                    from room_production_command_factory import ProductionCommandFactory, ProductionCommandSession
                owner = self.command_factory(self.graph, self.recorder)
                production = (type(self.command_factory) is ProductionCommandFactory
                    and type(owner) is ProductionCommandSession
                    and self.config['schema'] == 'home-live-session-config-3'
                    and self.command_factory.composition.graph is self.graph
                    and self.command_factory.declaration['source_bundle_digest']
                        == self.recorder.manifest['source_bundle_digest'])
                if (type(owner) is not CanonicalCommandSession and not production
                        or owner.journal is not self.graph.journal
                        or owner.worker_recorder is not self.recorder):
                    raise SupervisorRefusal("fixed_session_command_owner_required")
                self.command_owner = owner
                self._production_command = production
                routing = owner
            self.graph.journal.attach_recorder(routing.record)
            self.rpc = RecorderRPC(routing)
            recorder_port = self.rpc.start()
            if self.command_owner is not None:
                # Native operations remain on its owned thread. Recorder
                # routing is already installed before any of those effects.
                if not self.cancel.is_set():
                    self.command_owner.start()
                ready_deadline = time.monotonic() + 10
                while not self.cancel.is_set() and time.monotonic() < ready_deadline:
                    command_ready = self.command_owner.readiness()
                    if command_ready.get("status") == "ready":
                        break
                    if command_ready.get("status") not in {"starting", "waiting", "idle"}:
                        raise SupervisorRefusal("command_start_unconfirmed")
                    self.cancel.wait(.01)
                else:
                    if not self.cancel.is_set():
                        raise SupervisorRefusal("command_start_unconfirmed")
            if self.cancel.is_set():
                if self._terminal_status is not None and self.command_owner is not None:
                    # A bounded Stop already returned while construction was
                    # pending. Retain and retire this late object; never start
                    # the worker or upgrade that frozen incomplete outcome.
                    self.command_owner.quiesce()
                    self.command_owner.drain(timeout_s=self.config["drain_timeout_s"])
                    return
                self.stop(reason="preparation_cancelled")
                return
            control = self._control_authority()
            if control is not None:
                control.activate('runtime-control-start')
            if self.cancel.is_set():
                self.stop(reason="preparation_cancelled")
                return
            self.worker = self.worker_factory(self.graph, recorder_port)
            if self.cancel.is_set() and self._terminal_status is not None:
                # Stop returned unsafe while this constructor was still owned.
                # Retain/close its late child before acknowledging preparation.
                if self.worker is not None:
                    self.worker.stop_admission()
                    self.worker.close()
                if self.command_owner is not None:
                    self.command_owner.quiesce()
                    self.command_owner.drain(timeout_s=self.config["drain_timeout_s"])
                return
            if self.worker is None and self.config["mode"] != "development":
                raise SupervisorRefusal("worker_required_for_connected_session")
            if self.worker is not None and self.worker.readiness().get("status") != "ready":
                raise SupervisorRefusal("worker_not_ready")
            if self.command_factory is not None:
                self._command_ready = True
                self._command_setup_done.set()
                self._command_watchdog.cancel()
            if self.cancel.is_set():
                self.stop(reason="preparation_cancelled")
                return
            self.started = self.clock()
            self.state = "running"
            self._run()
        except Exception as error:
            allowed = {"resident_preparation_busy", "resident_preparation_cancelled", "resident_models_not_provisioned",
                       "resident_model_pin_mismatch", "resident_preparation_unconfirmed"}
            candidate = getattr(error, "reason", None)
            self.reason = (candidate if candidate in allowed else
                           "command_start_unconfirmed" if str(error) == "command_start_unconfirmed" else "session_start_unconfirmed")
            self.stop(reason=self.reason)
        finally:
            self._command_setup_done.set()
            if self._command_watchdog is not None:
                self._command_watchdog.cancel()

    def _run(self):
        reason = "session_complete"
        try:
            while not self.cancel.is_set() and self.cycles < self._cycle_limit:
                if self.clock() - self.started >= self.config["max_runtime_s"]:
                    reason = "session_runtime_limit"
                    break
                cycle_started = self.clock()
                outcome = self.graph.step()
                self.cycles += 1
                if self.worker is not None and callable(getattr(self.worker, "status", None)) and self.worker.status().get("status") == "failed":
                    reason = "worker_failed_closed"
                    break
                if self.command_owner is not None and self.command_owner.status().get("status") in {"failed", "incomplete", "unconfirmed"}:
                    reason = "command_operation_unconfirmed"
                    break
                if outcome.get("status") in {"failed", "stopped"}:
                    reason = (outcome['reason'] if self.poll_budget and outcome.get('reason') in {
                        'lab_poll_budget_exhausted', 'lab_capture_original_budget_exhausted'} else "source_stopped")
                    break
                if self.after_source_step is not None:
                    deadline = min(self.clock() + 2, self.started + self.config["max_runtime_s"])
                    try:
                        handoff = self.after_source_step(self.graph, self.worker, outcome,
                                                        cancel=self.cancel, deadline=deadline)
                    except Exception:
                        handoff = {"status": "unconfirmed"}
                    if self.cancel.is_set():
                        reason = "operator_stop"
                        break
                    if (not isinstance(handoff, dict) or handoff.get("status") != "settled"
                            or self.clock() > deadline):
                        reason = "source_worker_handoff_unconfirmed"
                        break
                remaining = self.config["max_runtime_s"] - (self.clock() - self.started)
                if remaining <= 0:
                    reason = "session_runtime_limit"
                    break
                if self.cycles < self._cycle_limit:
                    wait = self.config["interval_s"]
                    if self.fixed_period:
                        wait = max(0, wait - (self.clock() - cycle_started))
                    self.cancel.wait(min(wait, remaining))
            if self.poll_budget and reason == 'session_complete' and not self.cancel.is_set() and self.cycles >= self._cycle_limit:
                reason = 'session_poll_budget_exhausted'
        except Exception:
            reason = "source_operation_unconfirmed"
        self.stop(reason=reason)

    def _control_authority(self):
        journal = getattr(self.graph, 'journal', None)
        worker = getattr(getattr(journal, 'dynamic', None), 'worker', None)
        return getattr(worker, 'control_authority', None)

    def _command_preparation_timeout(self):
        if self._command_setup_done.is_set():
            return
        self._command_problem = True
        self.cancel.set()
        self.stop(reason="command_preparation_unconfirmed")

    def _command_incomplete(self, reason, receipt, *, withdrawal_receipt=None, terminal_cursor=0, worker_status="unconfirmed"):
        # Do not close Journal, RPC or recorders beneath an owned late writer.
        # This frozen outcome stays incomplete even if it subsequently settles.
        self.state, self.reason = "incomplete", reason
        self._command_problem, self._command_drain = True, receipt
        self.result = {"drain": {"schema": "home-acquisition-drain-1", "version": 1,
            "session_ref": self.config["session_ref"], "configuration_digest": self.config["configuration_digest"],
            "withdrawal_receipt": withdrawal_receipt, "recorder_status": "incomplete", "worker_status": worker_status,
            "terminal_cursor": terminal_cursor},
            "finalization": {"status": "unconfirmed", "reason": reason, "buffers_cleared": False}}
        self._terminal_status = self.status()
        return json.loads(canonical(self._terminal_status))

    def stop(self, reason="operator_stop"):
        if self._terminal_status is not None:
            return json.loads(canonical(self._terminal_status))
        if not self.stop_lock.acquire(False):
            return self.status()
        try:
            # Another Stop may have completed between the first cache check
            # and our acquisition. A terminal receipt never reopens cleanup.
            if self._terminal_status is not None:
                return json.loads(canonical(self._terminal_status))
            self.cancel.set()
            if self.command_owner is not None:
                try:
                    self.command_owner.quiesce()
                except Exception:
                    self._command_problem = True
                    self.reason = "command_quiesce_unconfirmed"
            if self.stop_preparation is not None:
                try:
                    self.stop_preparation()
                except Exception:
                    self.reason = "preparation_stop_unconfirmed"
            if self.command_factory is not None and self.state != "idle" and not self._command_setup_done.is_set():
                if threading.current_thread() is self.thread:
                    self._command_setup_done.set()
                elif not self._command_setup_done.wait(self.config["drain_timeout_s"]):
                    return self._command_incomplete("command_preparation_unconfirmed",
                        {"status": "unconfirmed", "safe_to_close_journal": False, "pending": True})
            if self.graph is None:
                idle = self.state == "idle"
                self.state = "stopped" if idle else "incomplete"
                if idle:
                    self._terminal_status = self.status()
                # A graph factory can still be returning after early Stop.
                # Do not freeze that interim state and skip its later cleanup.
                return self.status()
            self.state, self.reason = "stopping", reason
            deadline = self.clock() + self.config["drain_timeout_s"]
            real_deadline = time.monotonic() + self.config["drain_timeout_s"]
            quiescent = self.graph.quiesce(reason)
            while quiescent.get("status") != "quiescent" and self.clock() < deadline and time.monotonic() < real_deadline:
                self.sleep(.02)
                quiescent = self.graph.quiesce(reason)
            control_confirmed = True
            control = self._control_authority()
            if control is not None:
                # Only this cleanup worker may remain inside the actual SQL /
                # recorder call. Never pretend to cancel or close it on timeout.
                done, outcome = threading.Event(), {'confirmed':False}
                self._control_retirement_pending = True
                def retire_control():
                    try:
                        state = control.state()
                        if state['control_active'] or state['control_generation'] == 0:
                            control.retire('runtime-control-stop', reason)
                        outcome['confirmed'] = True
                    except Exception:
                        pass
                    finally:
                        done.set()
                self._control_retirement_thread = threading.Thread(target=retire_control,
                    name='den-control-retirement', daemon=True)
                self._control_retirement_thread.start()
                remaining = max(0, min(deadline-self.clock(), real_deadline-time.monotonic()))
                finished = done.wait(remaining)
                control_confirmed = finished and outcome['confirmed']
                if not control_confirmed:
                    # Terminate only the owned worker, without any pipe lock or
                    # wait. Late SQL/storage completion cannot upgrade this result.
                    stop_admission = getattr(self.worker, 'stop_admission', None)
                    if callable(stop_admission):
                        try:
                            stop_admission()
                        except Exception:
                            pass
                    self.reason, self.state = 'control_retirement_unconfirmed', 'incomplete'
                    self.result = {'drain': {'schema':'home-acquisition-drain-1','version':1,
                        'session_ref':self.config['session_ref'],'configuration_digest':self.config['configuration_digest'],
                        'withdrawal_receipt':None,'recorder_status':'incomplete','worker_status':'unconfirmed','terminal_cursor':0},
                        'finalization': {'status':'unconfirmed','reason':'control_retirement_unconfirmed','buffers_cleared':False}}
                    self._terminal_status = self.status()
                    return json.loads(canonical(self._terminal_status))
                self._control_retirement_pending = False
            withdrawal = self.graph.withdraw(reason) if quiescent.get("status") == "quiescent" else {"status": "unconfirmed", "withdrawal": "not_recorded", "receipt": None}
            # The graph's exact durable ACK, never an invented cursor or new write after drain.
            withdrawal_receipt = withdrawal.get("barrier_receipt", withdrawal.get("withdrawal_receipt", withdrawal.get("receipt")))
            worker_result = {"status": "settled", "input_cursor": (withdrawal_receipt or {}).get("cursor", 0), "worker_required": False} if self.worker is None else {"status": "unconfirmed"}
            if self.worker is not None:
                try:
                    worker_result = self.worker.drain(deadline_ms=max(1, int(max(0, deadline - self.clock()) * 1000)),
                        through_cursor=(withdrawal_receipt or {}).get("cursor", 0))
                except Exception:
                    pass
            worker_status = "settled" if worker_result.get("status") == "settled" and worker_result.get("input_cursor", 0) >= (withdrawal_receipt or {}).get("cursor", 0) else "unconfirmed"
            terminal_cursor = max(worker_result.get("input_cursor", 0), (withdrawal_receipt or {}).get("cursor", 0))
            command_result = None
            if self.command_owner is not None:
                try:
                    command_result = self.command_owner.drain(timeout_s=max(0, min(
                        deadline-self.clock(), real_deadline-time.monotonic())))
                except Exception:
                    command_result = {"status": "unconfirmed", "safe_to_close_journal": False, "pending": True}
                self._command_drain = command_result
                if command_result.get("safe_to_close_journal") is not True:
                    return self._command_incomplete("command_drain_unconfirmed", command_result,
                        withdrawal_receipt=withdrawal_receipt, terminal_cursor=terminal_cursor, worker_status=worker_status)
            assessment = getattr(self.graph, "assessment_recorder", None)
            if assessment is not None and self.assessment.get("status") != "retained":
                try:
                    if self.assessment_file is None:
                        raise SupervisorRefusal("assessment_output_unavailable")
                    exported = assessment.export()
                    assessment.write(self.assessment_file)
                    self.assessment = {"status": "retained", "records": len(exported["records"]), "complete": exported["complete"]}
                except Exception:
                    self.assessment = {"status": "incomplete", "reason": "assessment_output_unavailable"}
            complete = (control_confirmed and quiescent.get("status") == "quiescent" and withdrawal.get("withdrawal") == "recorded"
                        and worker_status == "settled" and self.recorder is not None and self.reason not in {
                            "session_start_unconfirmed", "source_operation_unconfirmed", "source_worker_handoff_unconfirmed",
                            "command_start_unconfirmed", "command_operation_unconfirmed", "command_quiesce_unconfirmed"})
            complete = complete and not self._command_problem and (command_result is None
                or command_result.get("status") == "drained"
                or self._production_command and command_result.get("status") == "complete")
            complete = complete and (self.command_factory is None or self._command_ready)
            complete = complete and self.assessment.get("status") != "incomplete"
            terminal = {"status": "complete" if complete else "incomplete", "reason": reason,
                        "terminal_cursor": terminal_cursor, "worker_status": worker_status,
                        "withdrawal_receipt": withdrawal_receipt}
            if command_result is not None:
                terminal["commands"] = command_result
            recorder_status = "incomplete"
            if self.recorder is not None:
                report = self.recorder.drain(terminal)
                self._recording_cache = dict(report)
                if report["status"] == "drained" and complete:
                    recorder_status = "drained"
            if self.worker is not None:
                self.worker.close()
            if self.rpc is not None:
                self.rpc.close()
            drain = {"schema": "home-acquisition-drain-1", "version": 1,
                     "session_ref": self.config["session_ref"], "configuration_digest": self.config["configuration_digest"],
                     "withdrawal_receipt": withdrawal_receipt, "recorder_status": recorder_status,
                     "worker_status": worker_status, "terminal_cursor": terminal_cursor}
            try:
                final = self.graph.finalize(reason=reason, drain_receipt=drain)
            except Exception:
                final = {"status": "unconfirmed"}
            if self.release is not None and final.get("buffers_cleared") is True:
                try:
                    self.release()
                except Exception:
                    final = {**final, "status": "unconfirmed", "reason": "model_release_unconfirmed"}
            self.result = {"drain": drain, "finalization": final}
            self.state = "stopped" if complete and recorder_status == "drained" and final.get("status") in {"stopped", "finalized"} else "incomplete"
            self._terminal_status = self.status()
            return json.loads(canonical(self._terminal_status))
        finally:
            self.stop_lock.release()

    def status(self):
        with self.lock:
            if self._terminal_status is not None:
                return json.loads(canonical(self._terminal_status))
            if self._control_retirement_pending:
                recording = {**(self._recording_cache or {}), 'status':'incomplete','reason':'control_retirement_pending'}
            else:
                recording = self.recorder.status() if self.recorder is not None else {"status":"unavailable"}
            value = {"schema": "home-live-session-status-1", "version": 1, "status": self.state, "reason": self.reason,
                    "session_ref": self.config["session_ref"], "configuration_digest": self.config["configuration_digest"],
                    "mode": self.config["mode"], "cycles": self.cycles,
                    "recording": recording,
                    "assessment": dict(self.assessment),
                    "drain": self.result["drain"] if self.result else None,
                    "physical_authority": False, "acceptance_eligible": False}
            if self.poll_budget:
                value['poll_budget'] = {key: self.config[key] for key in ('max_poll_attempts', 'max_capture_originals')}
            if self.command_factory is not None:
                value["commands"] = (self.command_owner.status() if self.command_owner is not None else
                    {"status": "unavailable", "reason": "command_owner_not_constructed"})
                value["command_drain"] = self._command_drain
                value["combined_replay"] = {"status": "unproven", "reason": "whole_lab_worker_canonical_native_join_required"}
            return value

    def control(self, command):
        if (not isinstance(command, dict) or set(command) != {"schema", "version", "id", "operation", "session_ref", "configuration_digest"}
                or command["schema"] != "home-live-session-command-1" or command["version"] != 1
                or command["session_ref"] != self.config["session_ref"] or command["configuration_digest"] != self.config["configuration_digest"]
                or command["operation"] not in {"start", "stop", "status", "read-results"}
                or not isinstance(command["id"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}", command["id"])):
            raise SupervisorRefusal("operator_command_binding_refused")
        with self.lock:
            prior = self.commands.get(command["id"])
            if prior:
                if prior[0] != digest(command):
                    raise SupervisorRefusal("operator_command_identity_conflict")
                return prior[1]
            if len(self.commands) >= 32:
                raise SupervisorRefusal("operator_command_budget_exhausted")
        operation = command["operation"]
        value = self.start() if operation == "start" else self.stop() if operation == "stop" else self.read_results() if operation == "read-results" else self.status()
        # Reads remain current; mutation exact retries retain their original result.
        if operation in {"start", "stop"}:
            self.commands[command["id"]] = (digest(command), value)
        return value

    def read_results(self):
        value = self.status()
        value["ordinary_replay"] = {"status": "unavailable", "reason": "session_not_terminal"}
        if self._terminal_status is not None and self._terminal_status['reason'] == 'control_retirement_unconfirmed':
            value['ordinary_replay'] = {'status':'incomplete','reason':'control_retirement_unconfirmed'}
            return value
        if self.state in {"stopped", "incomplete"} and self.recorder is not None and self.worker is not None and hasattr(self.worker, "node_runtime"):
            try:
                result = subprocess.run([self.worker.node_runtime, str(ROOT / "tools/den-live-runtime/replay.cjs"), str(self.recorder.path)],
                    cwd=ROOT, capture_output=True, text=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if len(result.stdout.encode()) > 65536:
                    raise ValueError()
                aggregate = json.loads(result.stdout)
                replay_version = 2 if self.config["schema"] == "home-live-session-config-3" else 1
                if (aggregate.get("schema") != "home-live-ordinary-replay-result-" + str(replay_version)
                        or aggregate.get("version") != replay_version):
                    raise ValueError()
                value["ordinary_replay"] = aggregate
            except (OSError, ValueError, subprocess.TimeoutExpired):
                value["ordinary_replay"] = {"status": "incomplete", "reason": "ordinary_replay_unavailable"}
        elif self.config["mode"] == "development" and self.state in {"stopped", "incomplete"}:
            value["ordinary_replay"] = {"status": "not_applicable", "reason": "producer_only_development"}
        return value


class SessionControlEdge:
    def __init__(self, app, *, supervisor, token_sha256):
        if not isinstance(token_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", token_sha256):
            raise SupervisorRefusal("operator_authority_required")
        self.app, self.supervisor, self.token_sha256 = app, supervisor, token_sha256

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not scope.get("path", "").startswith(CONTROL_PREFIX):
            return await self.app(scope, receive, send)
        status = 403
        try:
            headers = scope.get("headers", [])
            auth = [v.decode() for k, v in headers if k.lower() == b"authorization"]
            if len(auth) != 1 or not auth[0].startswith("Bearer ") or not hmac.compare_digest(hashlib.sha256(auth[0][7:].encode()).hexdigest(), self.token_sha256):
                raise SupervisorRefusal("operator_credential_refused")
            content_types = [v.decode() for k, v in headers if k.lower() == b"content-type"]
            if len(content_types) != 1 or content_types[0].split(";", 1)[0] != "application/json":
                raise SupervisorRefusal("operator_json_required")
            operation = scope["path"][len(CONTROL_PREFIX):]
            if scope["method"] != "POST" or scope.get("query_string") or operation not in {"start", "stop", "status", "read-results"}:
                raise SupervisorRefusal("operator_route_refused")
            raw = bytearray()
            while True:
                message = await asyncio.wait_for(receive(), 3)
                if message["type"] == "http.disconnect":
                    raise SupervisorRefusal("operator_disconnected")
                raw.extend(message.get("body", b""))
                if len(raw) > 4096:
                    raise SupervisorRefusal("operator_body_bound")
                if not message.get("more_body"):
                    break
            if __package__:
                from .archive import _json
            else:
                from archive import _json
            command = _json(raw)
            if command.get("operation") != operation:
                raise SupervisorRefusal("operator_route_binding_refused")
            value = await asyncio.to_thread(self.supervisor.control, command)
            status = 200
        except (ValueError, OSError, asyncio.TimeoutError) as error:
            value = {"schema": "home-live-session-error-1", "reason": str(error) if isinstance(error, SupervisorRefusal) else "operator_unavailable", "physical_authority": False}
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": canonical(value).encode()})
