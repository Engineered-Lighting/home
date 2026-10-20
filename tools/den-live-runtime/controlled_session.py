"""Standalone local owner composition with fixed external I/O and ordinary drain.

The JSONL pipe is a server-owned control channel. Browser reads and intents use
the existing TLS ContextEdge, fixed bridge and authenticated Home gateway.
"""
from __future__ import annotations

# The incumbent directory also contains operator.py. On direct execution keep
# it off the stdlib search path until Python has loaded its ordinary operator.
import sys
if __name__ == "__main__":
    _direct_script_directory = sys.path.pop(0)

import argparse
import asyncio
from copy import deepcopy
import hashlib
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
from urllib.parse import quote, urlencode, urlsplit

if __name__ == "__main__":
    sys.path.insert(0, _direct_script_directory)
from controlled_external_io import build_base_configuration, assemble_journal_grants, ROOT, SCENARIOS, CLARIFICATION_SCENARIOS, MANUAL_SCENARIOS
from archive import canonical, digest
from supervisor import compose_supervisor

ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}\Z")
SHA = re.compile(r"[a-f0-9]{64}\Z")


def _closed_json(raw):
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise ValueError("duplicate_json_key")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("finite_json_required")))


def load_prepared_room(path):
    """Read a fixed private preparation, never a code, model or endpoint path."""
    path = Path(path)
    try:
        info = path.lstat()
        if not path.is_absolute() or path.is_symlink() or not path.is_file() or info.st_nlink != 1 or info.st_size > 1024 * 1024:
            raise ValueError("prepared_room_refused")
        value = _closed_json(path.read_bytes())
    except (OSError, UnicodeError):
        raise ValueError("prepared_room_unavailable") from None
    keys = {"schema", "version", "pack_directory", "asset_selection", "resident_binding", "room_policy", "source_bundle_digest",
            "acquisition_binding", "resident_assets", "resident_source_paths"}
    manual = isinstance(value, dict) and value.get("schema") == "home-den-controlled-room-3" and type(value.get("version")) is int and value["version"] == 3
    successor = manual or isinstance(value, dict) and value.get("schema") == "home-den-controlled-room-2" and type(value.get("version")) is int and value["version"] == 2
    if successor:
        keys.add("clarification_policy")
    if manual:
        keys.add("manual_policy")
    if (not isinstance(value, dict) or set(value) != keys
            or not successor and (value.get("schema") != "home-den-controlled-room-1" or type(value.get("version")) is not int or value["version"] != 1)
            or successor and (not isinstance(value.get("clarification_policy"), dict) or not isinstance(value.get("resident_binding"), dict) or value["resident_binding"].get("version") != (10 if manual else 9))
            or manual and not isinstance(value.get("manual_policy"),dict)
            or not isinstance(value["pack_directory"], str) or not Path(value["pack_directory"]).is_absolute()
            or not isinstance(value["asset_selection"], dict) or not isinstance(value["resident_binding"], dict)
            or not isinstance(value["room_policy"], dict) or not isinstance(value["acquisition_binding"], dict)
            or not isinstance(value["resident_assets"], dict) or not isinstance(value["resident_source_paths"], list)
            or not SHA.fullmatch(value["source_bundle_digest"] or "")):
        raise ValueError("prepared_room_refused")
    return value


class ControlledSession:
    def __init__(self, *, bundle, output_directory, node_runtime, source_bundle_digest, session_limits=None):
        from live_acquisition import create_production_acquisition
        if not SHA.fullmatch(source_bundle_digest or "") or not Path(node_runtime).is_absolute():
            raise ValueError("controlled_composition_refused")
        manual = bundle["configuration"].get("version") == 11
        clarification = bundle["configuration"].get("version") in {10,11}
        if (manual != bool(bundle["worker_config"].get("manual_policy")) or clarification != bool(bundle["worker_config"].get("clarification_policy"))
                or bundle["external_io"].status()["scenarios"] != list(MANUAL_SCENARIOS if manual else CLARIFICATION_SCENARIOS if clarification else SCENARIOS)):
            raise ValueError("controlled_profile_scenarios_mismatch")
        self.output_directory = Path(output_directory).resolve()
        self.output_directory.mkdir(parents=True, exist_ok=True)
        self.bundle = bundle
        self.identity = deepcopy(bundle["worker_config"]["identity"])
        self.configuration_digest = digest(bundle["acquisition_config"])
        self.source_bundle_digest = source_bundle_digest
        self.node_runtime = node_runtime
        self.archive_file = self.output_directory / "controlled-session-originals.jsonl"
        self.publication_token, self.worker_token, self.human_token = (secrets.token_hex(32) for _ in range(3))
        self.commands, self.lock = {}, threading.RLock()
        self.pending_commands, self.command_cache_lock = {}, threading.Lock()
        self.edge_server = self.edge_thread = self.edge = None
        self.control_server = self.control_thread = None
        self.closed = False
        self.worker_reason = None
        self.worker_diagnostic = None
        self.source_handoffs = []
        configuration, worker = bundle["configuration"], deepcopy(bundle["worker_config"])
        worker["token"] = self.worker_token
        grants = assemble_journal_grants(configuration, worker, publication_token=self.publication_token,
            worker_token=self.worker_token, human_token=self.human_token)
        limits = {"max_runtime_s": 120, "max_cycles": 24, "interval_s": 1.5 if worker.get("clarification_policy") else 1, "drain_timeout_s": 8}
        if session_limits is not None:
            if not isinstance(session_limits, dict) or set(session_limits) != set(limits):
                raise ValueError("controlled_session_limits_refused")
            limits = dict(session_limits)
            if clarification and limits["interval_s"] != 1.5:
                raise ValueError("controlled_clarification_cadence_required")
        def preflight(_config):
            bundle["occupancy"].start()
            deadline = time.monotonic() + 2
            while bundle["occupancy"].reading()["state"] != "occupied" and time.monotonic() < deadline:
                if self.supervisor.cancel.wait(.01):
                    return {"status": "refused"}
            return {"status": "ready"} if bundle["occupancy"].reading()["state"] == "occupied" else {"status": "refused"}
        def graph_factory():
            return create_production_acquisition(bundle["acquisition_config"], live_config=configuration,
                journal_path=self.output_directory / "controlled-session.sqlite", journal_grants=grants,
                publication_token=self.publication_token, shutdown_owner="supervisor",
                **{key: bundle[key] for key in ("capture_policy", "capture_method", "spatial_policy", "interaction_policy", "lifecycle_policy",
                    "detector", "pose", "local_authority", "household_enabled", "occupancy", "camera_dependencies", "control_clock", "assessment_recorder", "rss")})
        runtime = {"schema": "home-live-runtime-config-1", "version": 1,
            "session": {"schema": "home-live-session-config-1", "version": 1, "session_ref": self.identity,
                "configuration_digest": self.configuration_digest, "mode": "controlled", **limits},
            "worker_config": worker, "node_runtime": node_runtime, "archive_file": str(self.archive_file),
            "assessment_file": str(self.output_directory / "controlled-assessment.json"), "source_bundle_digest": source_bundle_digest,
            "archive_limits": {"max_records": 4096, "max_bytes": 64 * 1024**2, "min_free_bytes": 1024 * 1024}}
        self.supervisor = compose_supervisor(runtime, graph_factory=graph_factory, preflight=preflight,
            stop_preparation=lambda: bundle["occupancy"].stop(timeout=1), release=lambda: bundle["occupancy"].stop(timeout=1),
            after_source_step=self._after_source_step, fixed_period=limits["interval_s"] >= 1)
        ordinary_worker_factory = self.supervisor.worker_factory
        def observed_worker_factory(graph, recorder_port):
            driver = ordinary_worker_factory(graph, recorder_port)
            ordinary_status = driver.status
            def status():
                value = ordinary_status()
                self.retain_worker_diagnostic(value.get("diagnostic"))
                reason = value.get("reason")
                if value.get("status") == "failed" and isinstance(reason, str) and ID.fullmatch(reason):
                    self.worker_reason = reason
                return value
            driver.status = status
            return driver
        self.supervisor.worker_factory = observed_worker_factory

    def retain_worker_diagnostic(self, value):
        """Keep the last bounded retry code in this private attempt only."""
        if (not isinstance(value, dict) or set(value) != {"status", "reason"}
                or any(not isinstance(value[key], str) or not ID.fullmatch(value[key]) for key in value)):
            return
        self.worker_diagnostic = dict(value)
        target = self.output_directory / "worker-status-diagnostic.json"
        temporary = self.output_directory / ".worker-status-diagnostic.tmp"
        temporary.write_text(canonical(value) + "\n", encoding="utf-8")
        temporary.replace(target)

    def _after_source_step(self, graph, worker, outcome, *, cancel, deadline):
        """Hold the next authored source frame for the normal worker's receipt.

        Neither read changes that worker's delivered input cut. Source clocks,
        semantic timers and atomic commit admission remain ordinary.
        """
        target, accepted = None, None
        def finish(status):
            if len(self.source_handoffs) < 24:
                self.source_handoffs.append({"status": status, "target_cursor": target, "accepted_input_cursor": accepted})
                temporary = self.output_directory / ".source-worker-handoffs.tmp"
                temporary.write_text(canonical({"schema": "home-den-source-worker-handoffs-1", "version": 1, "items": self.source_handoffs}) + "\n", encoding="utf-8")
                temporary.replace(self.output_directory / "source-worker-handoffs.json")
            return {"status": status}
        def read(route, token):
            if cancel.is_set() or time.monotonic() >= deadline:
                raise TimeoutError()
            if graph.address[0] != "127.0.0.1":
                raise ValueError("literal_loopback_required")
            connection = http.client.HTTPConnection(*graph.address, timeout=min(.25, deadline-time.monotonic()))
            response = transport = guard = None
            finished = threading.Event()
            try:
                # Socket inactivity alone does not bound a peer trickling its
                # headers/body. Keep the actual socket across HTTP/1.0 detach,
                # and interrupt every read at the absolute deadline or Stop.
                connection.connect()
                transport = connection.sock
                def interrupt_at_bound():
                    while not finished.is_set():
                        if cancel.is_set() or time.monotonic() >= deadline:
                            try: transport.shutdown(socket.SHUT_RDWR)
                            except OSError: pass
                            return
                        finished.wait(min(.01, max(0, deadline-time.monotonic())))
                guard = threading.Thread(target=interrupt_at_bound, name="home-den-handoff-deadline")
                guard.start()
                connection.request("GET", route, headers={"Authorization": "Bearer " + token})
                response = connection.getresponse()
                raw = response.read(1024*1024+1)
                if cancel.is_set() or time.monotonic() >= deadline or response.status != 200 or len(raw) > 1024*1024:
                    raise ValueError("source_worker_handoff_unconfirmed")
                return _closed_json(raw)
            finally:
                finished.set()
                if guard is not None: guard.join()
                if response is not None: response.close()
                connection.close()
                if transport is not None: transport.close()
        if cancel.is_set():
            return finish("cancelled")
        query = "?" + urlencode({key: self.identity[key] for key in ("site_id", "room_id")})
        try:
            snapshot = read("/api/home-context/v1/snapshot" + query, self.human_token)
            if (not isinstance(snapshot, dict) or snapshot.get("schema") != "home-context-snapshot-1"
                    or not isinstance(snapshot.get("stream_epoch"), str) or not ID.fullmatch(snapshot["stream_epoch"])
                    or type(snapshot.get("cursor")) is not int or snapshot["cursor"] < 0
                    or type(snapshot.get("revocation_generation")) is not int or snapshot["revocation_generation"] < 0
                    or not isinstance(snapshot.get("items"), list)):
                return finish("unconfirmed")
            target = snapshot["cursor"]
            expected = self.bundle["worker_config"]
            stage = 10 if expected.get("manual_policy") else 9 if expected.get("clarification_policy") else 8 if expected.get("room_policy") else 6
            while time.monotonic() < deadline and not cancel.is_set():
                current = read("/api/home-context/v1/worker-commits/" + quote(expected["worker_id"], safe="") + query, self.worker_token)
                if (not isinstance(current, dict) or current.get("schema") != "home-context-worker-checkpoint-1"
                        or current.get("stream_epoch") != snapshot["stream_epoch"] or current.get("worker_id") != expected["worker_id"]
                        or type(current.get("revocation_generation")) is not int or current["revocation_generation"] != snapshot["revocation_generation"]
                        or type(current.get("input_cursor")) is not int or current["input_cursor"] < 0):
                    return finish("unconfirmed")
                cp = current.get("checkpoint")
                if current["input_cursor"] >= target and isinstance(cp, dict):
                    if (cp.get("schema") != "home-live-worker-checkpoint-" + str(stage) or cp.get("version") != stage
                            or cp.get("scope") != self.identity or cp.get("worker_id") != expected["worker_id"]
                            or cp.get("manifest_ref") != expected["manifest_ref"]
                            or cp.get("config_digest") != expected["live_manifest"]["worker"]["config_digest"]
                            or type(cp.get("input_cursor")) is not int or cp["input_cursor"] != current["input_cursor"]
                            or type(cp.get("authority_generation")) is not int or cp["authority_generation"] != current["revocation_generation"]):
                        return finish("unconfirmed")
                    accepted = current["input_cursor"]
                    if cancel.is_set(): return finish("cancelled")
                    return finish("settled" if time.monotonic() < deadline else "unconfirmed")
                cancel.wait(min(.05, max(0, deadline-time.monotonic())))
        except (OSError, ValueError, TypeError, KeyError, http.client.HTTPException):
            pass
        return finish("cancelled" if cancel.is_set() else "unconfirmed")

    @classmethod
    def from_prepared(cls, path, *, output_directory, node_runtime):
        from room_configuration import upgrade_configuration
        prepared = load_prepared_room(path)
        identity = prepared["resident_binding"].get("scope")
        if (not isinstance(identity, dict) or set(identity) != {"site_id", "room_id", "session_id", "domain_epoch"}
                or identity.get("site_id") != "victoria" or identity.get("room_id") != "den"):
            raise ValueError("prepared_room_scope_refused")
        manual = prepared["version"] == 3
        clarification = prepared["version"] in {2,3}
        bundle = build_base_configuration(session_ref=identity, node_runtime=node_runtime,
            acquisition_binding=prepared.get("acquisition_binding"), clarification=clarification, manual=manual)
        configuration, worker = upgrade_configuration(bundle["configuration"], bundle["worker_config"], prepared["room_policy"],
            clarification_policy=prepared.get("clarification_policy"),manual_policy=prepared.get("manual_policy"))
        bundle.update(configuration=configuration, worker_config=worker)
        value = cls(bundle=bundle, output_directory=output_directory, node_runtime=node_runtime,
                    source_bundle_digest=prepared["source_bundle_digest"])
        value.prepared = prepared
        return value

    def status(self, *, results=False):
        raw = self.supervisor.read_results() if results else self.supervisor.status()
        record = raw["recording"].get("status", "unavailable")
        recorder = "not_started" if self.supervisor.recorder is None else "drained" if record == "drained" else "incomplete" if raw["status"] == "incomplete" else "ready"
        worker = "not_started" if self.supervisor.worker is None else "settled" if (raw.get("drain") or {}).get("worker_status") == "settled" else "ready" if raw["status"] == "running" else "unconfirmed"
        drain = raw.get("drain")
        public_drain = None if drain is None else {"status": "complete" if raw["status"] == "stopped" else "incomplete",
            "worker_status": drain["worker_status"], "recorder_status": drain["recorder_status"], "terminal_cursor": drain["terminal_cursor"]}
        replay = raw.get("ordinary_replay", {"status": "unavailable", "reason": "results_not_requested"})
        public_replay = {key: replay[key] for key in ("status", "reason", "commits") if key in replay}
        external = self.bundle["external_io"].status()
        return {"schema": "home-den-controlled-status-1", "version": 1, "status": raw["status"], "reason": self.worker_reason or raw["reason"],
            "scope": {key: self.identity[key] for key in ("site_id", "room_id")}, "session_ref": deepcopy(self.identity),
            "configuration_digest": self.configuration_digest, "source_mode": "controlled_external_io",
            "scenario": external["scenario"], "scenarios": external["scenarios"], "cycles": raw["cycles"],
            "readiness": {"room": "ready", "recorder": recorder, "worker": worker}, "drain": public_drain,
            "ordinary_replay": public_replay, "physical_authority": False, "acceptance_eligible": False}

    def control(self, command):
        if (not isinstance(command, dict) or set(command) != ({"id", "operation", "scenario"} if command.get("operation") == "scenario" else {"id", "operation"})
                or not isinstance(command.get("id"), str) or not ID.fullmatch(command["id"])
                or command.get("operation") not in {"start", "stop", "status", "read-results", "scenario"}):
            raise ValueError("controlled_command_refused")
        command = deepcopy(command)
        command_digest = digest(command)
        def prior_result():
            # Caller holds only the short cache lock. In-flight mutations also
            # own their exact identity, including against concurrent reads.
            prior = self.commands.get(command["id"])
            pending = self.pending_commands.get(command["id"])
            if prior is not None and prior[0] != command_digest or pending is not None and pending != command_digest:
                raise ValueError("controlled_command_identity_conflict")
            return deepcopy(prior[1]) if prior is not None else None
        if command["operation"] in {"status", "read-results"}:
            with self.command_cache_lock:
                prior = prior_result()
            if prior is not None:
                return prior
            # Reads must never queue behind owner Stop or its durable I/O.
            return self.status(results=command["operation"] == "read-results")
        with self.lock:
            with self.command_cache_lock:
                prior = prior_result()
                if prior is not None:
                    return prior
                # Keep one finite immutable slot for the first owner Stop.
                # Pending reservations count before execution, never twice.
                budget = 32 if command["operation"] == "stop" else 31
                if len(self.commands) + len(self.pending_commands) >= budget:
                    raise ValueError("controlled_command_budget_exhausted")
                self.pending_commands[command["id"]] = command_digest
            try:
                if command["operation"] == "scenario":
                    if self.supervisor.state not in {"idle", "starting", "running"}:
                        raise ValueError("controlled_session_terminal")
                    self.bundle["external_io"].set_scenario(command["scenario"])
                else:
                    self.supervisor.control({"schema": "home-live-session-command-1", "version": 1, **command,
                        "session_ref": deepcopy(self.identity), "configuration_digest": self.configuration_digest})
                value = self.status()
                with self.command_cache_lock:
                    self.commands[command["id"]] = (command_digest, deepcopy(value))
                    del self.pending_commands[command["id"]]
            except BaseException:
                with self.command_cache_lock:
                    self.pending_commands.pop(command["id"], None)
                raise
            return value

    def start_edge(self):
        """Bind the ordinary TLS edge; its journal capability is lazy and fixed."""
        from live_context_edge import ContextEdge
        if self.edge_server is not None:
            raise ValueError("controlled_edge_already_started")
        openssl = shutil.which("openssl")
        if not openssl:
            candidate = Path(r"C:\Program Files\Git\mingw64\bin\openssl.exe")
            openssl = str(candidate) if candidate.is_file() else None
        if not openssl:
            raise ValueError("existing_openssl_required")
        directory = self.output_directory / "edge-private"
        directory.mkdir(mode=0o700)
        def run(args):
            subprocess.run([openssl, *args], cwd=directory, check=True, capture_output=True, timeout=15,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        run(["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "ca.key", "-out", "ca.pem", "-days", "1",
             "-subj", "/CN=Home controlled local CA", "-addext", "basicConstraints=critical,CA:TRUE"])
        run(["req", "-newkey", "rsa:2048", "-nodes", "-keyout", "server.key", "-out", "server.csr", "-subj", "/CN=Home controlled context"])
        (directory / "ext").write_text("subjectAltName=IP:127.0.0.1\nbasicConstraints=CA:FALSE\nextendedKeyUsage=serverAuth\n")
        run(["x509", "-req", "-in", "server.csr", "-CA", "ca.pem", "-CAkey", "ca.key", "-CAcreateserial", "-out", "server.pem",
             "-days", "1", "-extfile", "ext"])
        binding = {"scope": deepcopy(self.identity), "manifest_ref": deepcopy(self.bundle["configuration"]["manifest_ref"])}
        async def denied(_scope, _receive, send):
            await send({"type": "http.response.start", "status": 403, "headers": []})
            await send({"type": "http.response.body", "body": b"{}"})
        self.edge = ContextEdge(denied, binding=binding, token_sha256=hashlib.sha256(self.human_token.encode()).hexdigest(),
            journal_address=lambda: self.supervisor.graph.address if self.supervisor.state == "running" else None,
            intent_version=4 if self.bundle["worker_config"].get("manual_policy") else 3 if self.bundle["worker_config"].get("clarification_policy") else 2)
        edge = self.edge
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def setup(self):
                # Begin before the lazy handshake and HTTP parsing. A socket
                # inactivity timeout alone would renew on every peer chunk.
                self.deadline_monotonic = time.monotonic() + 3
                self.deadline_finished = threading.Event()
                self.deadline_guard = threading.Thread(target=self.interrupt_at_deadline,
                    name="home-den-context-tls-deadline")
                self.deadline_guard.start()
                try:
                    super().setup()
                    self.connection.settimeout(3)
                except BaseException:
                    self.deadline_finished.set()
                    self.deadline_guard.join()
                    raise
            def interrupt_at_deadline(self):
                if not self.deadline_finished.wait(max(0, self.deadline_monotonic - time.monotonic())):
                    try: self.request.shutdown(socket.SHUT_RDWR)
                    except OSError: pass
            def handle(self):
                try:
                    super().handle()
                except OSError:
                    self.close_connection = True
            def finish(self):
                try:
                    super().finish()
                except OSError:
                    pass
                finally:
                    self.deadline_finished.set()
                    self.deadline_guard.join()
            def require_remaining(self):
                if time.monotonic() >= self.deadline_monotonic:
                    raise TimeoutError("controlled_context_deadline")
            def do_GET(self): self.dispatch()
            def do_POST(self): self.dispatch()
            def dispatch(self):
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 <= length <= 65536 or len(self.path) > 4096:
                        raise ValueError()
                    body, parsed, output = self.rfile.read(length), urlsplit(self.path), []
                    self.require_remaining()
                    if len(body) != length:
                        raise ValueError()
                    async def receive(): return {"type": "http.request", "body": body, "more_body": False}
                    async def send(value): output.append(value)
                    asyncio.run(edge({"type": "http", "method": self.command, "path": parsed.path, "raw_path": parsed.path.encode(),
                        "query_string": parsed.query.encode(), "headers": [(k.lower().encode(), v.encode()) for k, v in self.headers.items()]}, receive, send,
                        deadline_monotonic=self.deadline_monotonic))
                    self.require_remaining()
                    head, content = output
                    self.send_response(head["status"])
                    for key, value in head["headers"]: self.send_header(key.decode(), value.decode())
                    self.send_header("Content-Length", str(len(content["body"]))); self.end_headers(); self.wfile.write(content["body"])
                except (ValueError, OSError):
                    self.close_connection = True
        self.edge_server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.edge_server.daemon_threads = True
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.minimum_version = ssl.TLSVersion.TLSv1_2
        tls.load_cert_chain(str(directory / "server.pem"), str(directory / "server.key"))
        self.edge_server.socket = tls.wrap_socket(self.edge_server.socket, server_side=True, do_handshake_on_connect=False)
        self.edge_thread = threading.Thread(target=self.edge_server.serve_forever, kwargs={"poll_interval": .05}, daemon=True)
        self.edge_thread.start()
        token_file = directory / "context-token"
        with os.fdopen(os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
            stream.write(self.human_token)
        return {"url": "https://127.0.0.1:" + str(self.edge_server.server_port), "ca_file": str(directory / "ca.pem"),
            "token_file": str(token_file), "binding": binding, "scope": deepcopy(self.identity), "manifest_ref": deepcopy(binding["manifest_ref"]),
            "live_manifest": deepcopy(self.bundle["configuration"]["manifest"]),
            "registration": deepcopy(self.bundle["worker_config"]["delivery_policy"]["registration"]), "actor": "controlled-owner"}

    def start_control(self):
        """Private fixed loopback hop for the authenticated Home control route."""
        if self.control_server is not None:
            raise ValueError("controlled_control_already_started")
        nonce, session = secrets.token_hex(32), self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def setup(self):
                super().setup()
                self.connection.settimeout(3)
            def do_GET(self): self.dispatch()
            def do_POST(self): self.dispatch()
            def dispatch(self):
                status, value = 403, {"schema": "home-den-controlled-error-1", "reason": "controlled_internal_authority_refused"}
                try:
                    supplied = self.headers.get_all("X-Home-Den-Internal", [])
                    if (self.client_address[0] != "127.0.0.1" or len(supplied) != 1
                            or not hmac.compare_digest(supplied[0], nonce)):
                        raise ValueError("controlled_internal_authority_refused")
                    if self.command != "POST" or self.path != "/control":
                        status = 404
                        raise ValueError("controlled_route_refused")
                    lengths, types = self.headers.get_all("Content-Length", []), self.headers.get_all("Content-Type", [])
                    if (len(lengths) != 1 or not re.fullmatch(r"0|[1-9][0-9]{0,3}", lengths[0])
                            or not 0 < int(lengths[0]) <= 4096 or len(types) != 1 or types[0].split(";", 1)[0] != "application/json"
                            or self.headers.get("Transfer-Encoding") is not None):
                        raise ValueError("controlled_body_refused")
                    value = session.control(_closed_json(self.rfile.read(int(lengths[0]))))
                    status = 200
                except (ValueError, UnicodeError) as error:
                    reason = str(error)
                    value = {"schema": "home-den-controlled-error-1", "reason": reason if ID.fullmatch(reason) else "controlled_command_refused"}
                raw = canonical(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers(); self.wfile.write(raw)
        self.control_server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.control_server.daemon_threads = True
        self.control_thread = threading.Thread(target=self.control_server.serve_forever, kwargs={"poll_interval": .05}, daemon=True)
        self.control_thread.start()
        return {"upstream": "http://127.0.0.1:" + str(self.control_server.server_port) + "/control", "server_nonce": nonce}

    def close(self):
        if not self.closed:
            self.closed = True
            self.supervisor.stop(reason="operator_stop")
            if self.supervisor.thread is not None and self.supervisor.thread is not threading.current_thread():
                self.supervisor.thread.join(12)
            self.bundle["occupancy"].stop(timeout=1)
            if self.edge is not None: self.edge.invalidate()
            if self.edge_server is not None:
                self.edge_server.shutdown(); self.edge_server.server_close(); self.edge_thread.join(2)
            if self.control_server is not None:
                self.control_server.shutdown(); self.control_server.server_close(); self.control_thread.join(2)
        return self.status(results=True)


def main():
    parser = argparse.ArgumentParser(description="Fixed local controlled owner runtime; no real external I/O")
    parser.add_argument("--prepared-room")
    parser.add_argument("--output-directory")
    parser.add_argument("--node-runtime", required=True)
    parser.add_argument("--describe-base-binding", action="store_true")
    parser.add_argument("--session-ref")
    args = parser.parse_args()
    session = None
    def emit(value): print(canonical(value), flush=True)
    try:
        if args.describe_base_binding:
            identity = _closed_json(args.session_ref or "null")
            if not isinstance(identity, dict) or set(identity) != {"site_id", "room_id", "session_id", "domain_epoch"}:
                raise ValueError("controlled_scope_refused")
            bundle = build_base_configuration(session_ref=identity, node_runtime=args.node_runtime)
            emit({"schema": "home-den-controlled-base-binding-1", "version": 1, "session_ref": identity,
                "capture_registration": bundle["capture_policy"]["registration"], "projection": bundle["capture_policy"]["projection"],
                "mount_signature": bundle["capture_policy"]["mount_signature"], "registration": bundle["worker_config"]["delivery_policy"]["registration"],
                "delivery_policy": bundle["worker_config"]["delivery_policy"], "lifecycle_policy": bundle["worker_config"]["lifecycle_policy"],
                "scene_policy": bundle["worker_config"]["scene_policy"],
                "source_policy": next(iter(bundle["configuration"]["manifest"]["sources"].values()))})
            return 0
        if not args.prepared_room or not args.output_directory:
            raise ValueError("protected_prepared_room_required")
        session = ControlledSession.from_prepared(args.prepared_room, output_directory=args.output_directory, node_runtime=args.node_runtime)
        edge = session.start_edge()
        control = session.start_control()
        emit({"ok": True, "value": {"edge": edge, "control": control, "status": session.status()}})
        for raw in sys.stdin:
            try:
                if len(raw.encode()) > 4096: raise ValueError("controlled_command_refused")
                request = _closed_json(raw)
                if not isinstance(request, dict): raise ValueError("controlled_command_refused")
                if set(request) == {"operation", "command"} and request["operation"] == "control":
                    emit({"ok": True, "value": session.control(request["command"])})
                elif request == {"operation": "close"}:
                    emit({"ok": True, "value": session.close()}); break
                else: raise ValueError("controlled_command_refused")
            except ValueError as error:
                reason = str(error)
                emit({"ok": False, "reason": reason if ID.fullmatch(reason) else "controlled_command_refused"})
    except (ValueError, OSError, subprocess.SubprocessError):
        emit({"ok": False, "reason": "controlled_owner_entry_refused"})
        return 2
    finally:
        if session is not None: session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
