"""Public owner commands against the actual supervisor and authored I/O ports."""
import json
from contextlib import ExitStack
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ssl
import subprocess
from pathlib import Path
import shutil
import sys
import tempfile
import time
import threading
from types import SimpleNamespace
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from controlled_external_io import build_base_configuration
from controlled_session import ControlledSession, load_prepared_room


class ControlledSessionTests(unittest.TestCase):
    def test_source_handoff_uses_separate_existing_read_capabilities_and_rejects_wrong_epoch(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent, prefix="controlled-handoff-") as directory:
            bundle = build_base_configuration(session_ref={"site_id": "authored-site", "room_id": "authored-room",
                "session_id": "authored-handoff-session", "domain_epoch": "authored-handoff-domain"}, node_runtime=shutil.which("node"))
            session = ControlledSession(bundle=bundle, output_directory=directory, node_runtime=shutil.which("node"), source_bundle_digest="b" * 64)
            requests, fault = [], [None]
            request_started, stream_started = threading.Event(), threading.Event()
            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *_): pass
                def do_GET(self):
                    requests.append((self.path, self.headers.get("Authorization")))
                    if fault[0] == "pending_http":
                        request_started.set()
                        time.sleep(.4)
                    if self.path.startswith("/api/home-context/v1/snapshot?"):
                        value = {"schema": "home-context-snapshot-1", "stream_epoch": "authored-stream", "cursor": 7, "items": [], "revocation_generation": 0}
                    else:
                        value = {"schema": "home-context-worker-checkpoint-1", "stream_epoch": "wrong-stream" if fault[0] == "epoch" else "authored-stream",
                            "worker_id": bundle["worker_config"]["worker_id"], "input_cursor": 7, "context_seq": 1, "revocation_generation": 1 if fault[0] == "generation" else 0,
                            "checkpoint": {"schema": "home-live-worker-checkpoint-6", "version": 6, "scope": session.identity,
                                "worker_id": bundle["worker_config"]["worker_id"], "input_cursor": 7, "authority_generation": 0,
                                "manifest_ref": bundle["worker_config"]["manifest_ref"], "config_digest": bundle["worker_config"]["live_manifest"]["worker"]["config_digest"]}}
                        if fault[0] == "session": value["checkpoint"] = {**value["checkpoint"], "scope": {**session.identity, "session_id": "wrong-session"}}
                        if fault[0] == "configuration": value["checkpoint"]["config_digest"] = "f" * 64
                        if fault[0] == "boolean_generation": value["revocation_generation"] = False
                        if fault[0] == "boolean_authority": value["checkpoint"]["authority_generation"] = False
                    raw = json.dumps(value).encode()
                    try:
                        if fault[0] == "slow_headers":
                            self.connection.sendall(b"HTTP/1.0 200 OK\r\nX-Slow: ")
                            for _ in range(80): self.connection.sendall(b"x"); time.sleep(.01)
                            return
                        self.send_response(200); self.send_header("Content-Length", str(len(raw))); self.end_headers()
                        if fault[0] == "slow_stream":
                            for byte in raw:
                                self.wfile.write(bytes([byte])); self.wfile.flush(); stream_started.set(); time.sleep(.01)
                        else:
                            self.wfile.write(raw)
                    except (OSError, ConnectionError):
                        pass  # A timed-out local read must close its socket.
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                graph = SimpleNamespace(address=server.server_address)
                cancel = threading.Event()
                self.assertEqual(session._after_source_step(graph, None, {}, cancel=cancel, deadline=time.monotonic()+2), {"status": "settled"})
                self.assertEqual([token for _, token in requests], ["Bearer " + session.human_token, "Bearer " + session.worker_token])
                self.assertTrue(requests[1][0].startswith("/api/home-context/v1/worker-commits/controlled-worker?"))
                self.assertFalse(any("live-input-cut" in route for route, _ in requests))
                for fault[0] in ["epoch", "session", "configuration", "generation", "boolean_generation", "boolean_authority"]:
                    self.assertEqual(session._after_source_step(graph, None, {}, cancel=cancel, deadline=time.monotonic()+2), {"status": "unconfirmed"}, fault[0])
                count = len(requests); cancel.set()
                self.assertEqual(session._after_source_step(graph, None, {}, cancel=cancel, deadline=time.monotonic()+2), {"status": "cancelled"})
                self.assertEqual(len(requests), count)
                cancel.clear()
                self.assertEqual(session._after_source_step(graph, None, {}, cancel=cancel, deadline=time.monotonic()-.001), {"status": "unconfirmed"})
                self.assertEqual(len(requests), count)
                fault[0] = "pending_http"
                started = time.monotonic()
                self.assertEqual(session._after_source_step(graph, None, {}, cancel=cancel, deadline=started+.05), {"status": "unconfirmed"})
                self.assertLess(time.monotonic()-started, .3)
                request_started.clear(); answer = []
                call = threading.Thread(target=lambda: answer.append(session._after_source_step(graph, None, {}, cancel=cancel, deadline=time.monotonic()+2)))
                started = time.monotonic(); call.start(); self.assertTrue(request_started.wait(.5)); cancel.set(); call.join(.75)
                self.assertEqual(answer, [{"status": "cancelled"}]); self.assertLess(time.monotonic()-started, .75)
                cancel.clear()
                for fault[0] in ["slow_headers", "slow_stream"]:
                    started = time.monotonic()
                    self.assertEqual(session._after_source_step(graph, None, {}, cancel=cancel, deadline=started+.12), {"status": "unconfirmed"})
                    self.assertLess(time.monotonic()-started, .5, "continuous byte trickles cannot extend the absolute source handoff deadline")
                fault[0] = "slow_stream"; stream_started.clear(); answer.clear()
                call = threading.Thread(target=lambda: answer.append(session._after_source_step(graph, None, {}, cancel=cancel, deadline=time.monotonic()+2)))
                started = time.monotonic(); call.start(); self.assertTrue(stream_started.wait(.5)); cancel.set(); call.join(.75)
                self.assertEqual(answer, [{"status": "cancelled"}]); self.assertLess(time.monotonic()-started, .75)
                self.assertFalse(any(thread.name == "home-den-handoff-deadline" for thread in threading.enumerate()))
            finally:
                server.shutdown(); server.server_close(); thread.join(2); session.close()

    def test_worker_retry_diagnostic_survives_terminal_status_without_public_disclosure(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent, prefix="controlled-diagnostic-") as directory:
            bundle = build_base_configuration(session_ref={"site_id": "authored-site", "room_id": "authored-room",
                "session_id": "authored-diagnostic-session", "domain_epoch": "authored-diagnostic-domain"}, node_runtime=shutil.which("node"))
            session = ControlledSession(bundle=bundle, output_directory=directory, node_runtime=shutil.which("node"), source_bundle_digest="b" * 64)
            try:
                session.retain_worker_diagnostic({"status": "input_cut_changed", "reason": "room_output_mismatch"})
                session.retain_worker_diagnostic(None)
                session.retain_worker_diagnostic({"status": "input_cut_changed", "reason": "private raw details must never persist"})
                public = session.control({"id": "diagnostic-stop", "operation": "stop"})
                retained = json.loads((Path(directory) / "worker-status-diagnostic.json").read_text())
                self.assertEqual(retained, {"status": "input_cut_changed", "reason": "room_output_mismatch"})
                self.assertNotIn("diagnostic", public)
                self.assertNotIn("room_output_mismatch", json.dumps(public))
            finally:
                session.close()

    def test_finite_mutation_history_reserves_first_stop_and_keeps_reads_available(self):
        with ExitStack() as cleanup:
            directory = cleanup.enter_context(tempfile.TemporaryDirectory(dir=Path(__file__).parent, prefix="controlled-stop-budget-"))
            bundle = build_base_configuration(session_ref={"site_id": "authored-site", "room_id": "authored-room",
                "session_id": "authored-stop-session", "domain_epoch": "authored-stop-domain"}, node_runtime=shutil.which("node"))
            session = ControlledSession(bundle=bundle, output_directory=directory, node_runtime=shutil.which("node"),source_bundle_digest="b" * 64)
            cleanup.callback(session.close)
            for index in range(31):
                session.control({"id": "scenario-" + str(index), "operation": "scenario", "scenario": "presence"})
            with self.assertRaisesRegex(ValueError, "controlled_command_budget_exhausted"):
                session.control({"id": "excess-scenario", "operation": "scenario", "scenario": "presence"})
            stopped = session.control({"id": "reserved-stop", "operation": "stop"})
            self.assertEqual(stopped["status"], "stopped")
            self.assertEqual(session.control({"id": "reserved-stop", "operation": "stop"}), stopped)
            with self.assertRaisesRegex(ValueError, "controlled_command_identity_conflict"):
                session.control({"id": "reserved-stop", "operation": "scenario", "scenario": "empty"})
            self.assertEqual(session.control({"id": "post-stop-status", "operation": "status"})["status"], "stopped")
            self.assertEqual(session.control({"id": "post-stop-results", "operation": "read-results"})["status"], "stopped")

    def test_room9_actual_factory_continuous_clock_commits_four_fixture_output_and_replays(self):
        from copy import deepcopy
        from room_configuration import upgrade_configuration
        with ExitStack() as cleanup:
            directory = Path(__file__).resolve().parents[2] / "artifacts" / ("controlled-room9-" + uuid.uuid4().hex)
            directory.mkdir(parents=True)
            print("authored room9 evidence:", directory, flush=True)
            bundle = build_base_configuration(session_ref={"site_id": "authored-site", "room_id": "authored-room",
                "session_id": "authored-room9-session", "domain_epoch": "authored-room9-domain"}, node_runtime=shutil.which("node"))
            root = Path(__file__).resolve().parents[2]
            policy = json.loads(subprocess.check_output([shutil.which("node"), "-e",
                "process.stdout.write(JSON.stringify(require('./tools/fixtures/home-live-room.cjs').policy()))"], cwd=root, text=True))
            policy["registration"] = deepcopy(bundle["worker_config"]["delivery_policy"]["registration"])
            def current(value):
                if isinstance(value, dict):
                    for key in value:
                        if key == "valid_until": value[key] = time.time() + 180
                        else: current(value[key])
                elif isinstance(value, list):
                    for row in value: current(row)
            current(policy)
            bundle["configuration"], bundle["worker_config"] = upgrade_configuration(bundle["configuration"], bundle["worker_config"], policy)
            bundle["external_io"].set_scenario("presence")
            session = ControlledSession(bundle=bundle, output_directory=directory, node_runtime=shutil.which("node"),
                source_bundle_digest="b" * 64, session_limits={"max_runtime_s": 15, "max_cycles": 5, "interval_s": .5, "drain_timeout_s": 8})
            cleanup.callback(session.close)
            session.control({"id": "room9-owner-start", "operation": "start"})
            deadline = time.monotonic() + 20
            while session.status()["status"] not in {"stopped", "incomplete", "refused"} and time.monotonic() < deadline:
                time.sleep(.05)
            final = session.status(results=True)
            self.assertEqual(final["status"], "stopped", final)
            rows = [json.loads(line)["record"] for line in session.archive_file.read_text().splitlines()]
            decisions = [row["data"]["prepared"]["decision"] for row in rows if row["kind"] == "worker_prepared"]
            self.assertTrue(decisions)
            self.assertTrue(all(row["schema"] == "home-live-shadow-decision-8" for row in decisions))
            positive = [row for row in decisions if all(len(fixture["domains"]) == 8
                and all(domain["status"] == "modeled" and domain["sample"]["brightness_fraction"] > 0
                        for domain in fixture["domains"] if domain["domain"] != "spotlight")
                for fixture in row["room_output"]["fixtures"])]
            self.assertTrue(positive, decisions[-1])
            self.assertEqual(len(positive[-1]["room_output"]["fixtures"]), 4)
            self.assertTrue(any(hazard["kind"] == "unreconciled_session_history" for hazard in positive[-1]["person_hazards"]["items"]))
            self.assertTrue(all(fixture["domains"][-1]["sample"] is None for fixture in positive[-1]["room_output"]["fixtures"]))
            self.assertEqual(final["ordinary_replay"]["status"], "reproduced", final)
            # Historical output is not sufficient when a causal original or
            # independently bracketed consumer receipt has been removed.
            from archive import canonical, digest
            envelopes = [json.loads(line) for line in session.archive_file.read_text().splitlines()]
            def rewrite(name, mutate):
                altered = deepcopy(envelopes)
                mutate(altered)
                previous = None
                for index, row in enumerate(altered):
                    row.update(seq=index, previous_digest=previous)
                    row.pop("digest", None)
                    row["digest"] = digest(row)
                    previous = row["digest"]
                target = directory / name
                target.write_text("".join(canonical(row) + "\n" for row in altered))
                result = subprocess.run([shutil.which("node"), str(root / "tools/den-live-runtime/replay.cjs"), str(target)],
                    cwd=root, capture_output=True, text=True, timeout=12)
                self.assertEqual(result.returncode, 2, result.stdout)
                value = json.loads(result.stdout)
                self.assertEqual(value["status"], "incomplete", value)
                return value
            def drop_bounds(rows):
                next(row["record"]["data"] for row in rows if row["record"]["kind"] == "worker_cut").pop("receipt_clock_bounds")
            self.assertEqual(rewrite("missing-consumer-bounds.jsonl", drop_bounds)["reason"], "archive_missing_consumer_clock_bounds")
            def drop_original(rows):
                first = next(row["record"]["data"]["original_receipts"][0]["ref"] for row in rows
                    if row["record"]["kind"] == "journal_prepare" and row["record"]["data"]["original_receipts"])
                for row in rows:
                    if row["record"]["kind"] == "journal_prepare":
                        data = row["record"]["data"]
                        data["original_receipts"] = [original for original in data["original_receipts"] if original["ref"] != first]
            self.assertEqual(rewrite("missing-causal-original.jsonl", drop_original)["reason"], "archive_missing_original_receipt")

    def test_tls_edge_waits_for_readiness_and_admits_only_public_context_routes(self):
        from live_context_edge import binding_digest
        with ExitStack() as cleanup:
            directory = cleanup.enter_context(tempfile.TemporaryDirectory(dir=Path(__file__).parent, prefix="controlled-owner-tls-"))
            bundle = build_base_configuration(session_ref={"site_id": "authored-site", "room_id": "authored-room",
                "session_id": "authored-tls-session", "domain_epoch": "authored-tls-domain"}, node_runtime=shutil.which("node"))
            session = ControlledSession(bundle=bundle, output_directory=directory, node_runtime=shutil.which("node"),
                source_bundle_digest="b" * 64, session_limits={"max_runtime_s": 15, "max_cycles": 8, "interval_s": .5, "drain_timeout_s": 8})
            cleanup.callback(session.close)
            control = session.start_control()
            port = int(control["upstream"].split(":")[2].split("/")[0])
            def command(body, nonce):
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
                try:
                    connection.request("POST", "/control", json.dumps(body), headers={"Content-Type": "application/json", "X-Home-Den-Internal": nonce})
                    response = connection.getresponse()
                    return response.status, json.loads(response.read())
                finally:
                    connection.close()
            self.assertEqual(command({"id": "private-read", "operation": "status"}, "wrong")[0], 403)
            status, public = command({"id": "private-read", "operation": "status"}, control["server_nonce"])
            self.assertEqual(status, 200)
            self.assertEqual(public["status"], "idle")
            edge = session.start_edge()
            context = ssl.create_default_context(cafile=edge["ca_file"])
            headers = {"Authorization": "Bearer " + Path(edge["token_file"]).read_text(), "X-Home-Context-Binding": binding_digest(edge["binding"])}
            def request(path, authenticated=True):
                connection = http.client.HTTPSConnection("127.0.0.1", int(edge["url"].rsplit(":", 1)[1]), context=context, timeout=3)
                try:
                    connection.request("GET", path, headers=headers if authenticated else {})
                    response = connection.getresponse()
                    return response.status, json.loads(response.read())
                finally:
                    connection.close()
            route = "/api/home-context/v1/snapshot?site_id=authored-site&room_id=authored-room"
            self.assertEqual(request(route)[0], 503)
            self.assertEqual(request(route, False)[0], 401)
            session.control({"id": "tls-owner-start", "operation": "start"})
            deadline = time.monotonic() + 8
            while session.status()["status"] == "starting" and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertEqual(session.status()["status"], "running", session.status())
            status, snapshot = request(route)
            self.assertEqual(status, 200, snapshot)
            self.assertTrue(snapshot["service_clock"]["qualified"])
            private = "/api/home-context/v1/live-input-cut?site_id=authored-site&room_id=authored-room"
            self.assertEqual(request(private)[0], 404)
            wrong = "/api/home-context/v1/snapshot?site_id=wrong&room_id=authored-room"
            self.assertEqual(request(wrong)[0], 403)
            session.control({"id": "tls-owner-stop", "operation": "stop"})
            self.assertEqual(request(route)[0], 503)
            self.assertEqual(session.status(results=True)["ordinary_replay"]["status"], "reproduced")

    def test_prepared_owner_entry_refuses_missing_or_open_room_documents(self):
        with self.assertRaisesRegex(ValueError, "prepared_room_unavailable"):
            load_prepared_room(Path(__file__).parent / "missing-protected-prepared-room.json")
        with ExitStack() as cleanup:
            directory = cleanup.enter_context(tempfile.TemporaryDirectory(dir=Path(__file__).parent, prefix="controlled-owner-"))
            document = Path(directory) / "open.json"
            document.write_text('{"schema":"home-den-controlled-room-1","version":1,"extra":true}')
            with self.assertRaisesRegex(ValueError, "prepared_room_refused"):
                load_prepared_room(document)

    def test_owner_commands_drive_true_recorder_worker_and_terminal_replay(self):
        with ExitStack() as cleanup:
            directory = cleanup.enter_context(tempfile.TemporaryDirectory(dir=Path(__file__).parent, prefix="controlled-owner-"))
            bundle = build_base_configuration(session_ref={"site_id": "authored-site", "room_id": "authored-room",
                "session_id": "authored-owner-session", "domain_epoch": "authored-owner-domain"}, node_runtime=shutil.which("node"))
            session = ControlledSession(bundle=bundle, output_directory=directory, node_runtime=shutil.which("node"),
                source_bundle_digest="b" * 64, session_limits={"max_runtime_s": 12, "max_cycles": 3, "interval_s": .5, "drain_timeout_s": 8})
            cleanup.callback(session.close)
            self.assertEqual(session.control({"id": "owner-status", "operation": "status"})["status"], "idle")
            with self.assertRaisesRegex(ValueError, "controlled_command_refused"):
                session.control({"id": "escape", "operation": "status", "token": "not-accepted"})
            started = session.control({"id": "owner-start", "operation": "start"})
            self.assertEqual(session.control({"id": "owner-start", "operation": "start"}), started)
            deadline = time.monotonic() + 20
            while session.status()["status"] not in {"stopped", "incomplete", "refused"} and time.monotonic() < deadline:
                time.sleep(.05)
            final = session.control({"id": "owner-results", "operation": "read-results"})
            self.assertEqual(final["status"], "stopped", final)
            self.assertEqual(final["readiness"]["recorder"], "drained", final)
            self.assertEqual(final["drain"]["worker_status"], "settled", final)
            self.assertEqual(final["ordinary_replay"]["status"], "reproduced", final)
            rows = [json.loads(line)["record"] for line in session.archive_file.read_text().splitlines()]
            self.assertEqual(rows[0]["kind"], "session_manifest")
            self.assertTrue(any(row["kind"] == "worker_prepared" for row in rows))
            self.assertTrue(any(row["kind"] == "journal_prepare" for row in rows))
            session.close()


if __name__ == "__main__":
    unittest.main()
