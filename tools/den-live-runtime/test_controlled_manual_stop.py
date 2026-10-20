"""Owner reads remain current while real manual retirement storage is pending."""
from copy import deepcopy
import http.client
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import unittest
import uuid
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from controlled_session import ControlledSession
from test_controlled_manual_session import bundle_for_manual


class ControlledManualStopTests(unittest.TestCase):
    def successful_stop_case(self, *, delayed_source_finally):
        directory = Path(__file__).resolve().parents[2] / "artifacts" / ("controlled-manual-terminal-" + uuid.uuid4().hex)
        directory.mkdir(parents=True)
        print("authored manual terminal evidence:", directory, flush=True)
        bundle = bundle_for_manual()
        bundle["external_io"].set_scenario("perception_unavailable")
        session = ControlledSession(bundle=bundle, output_directory=directory, node_runtime=shutil.which("node"),
            source_bundle_digest="c" * 64)
        entered, release = threading.Event(), threading.Event()
        if delayed_source_finally:
            ordinary_handoff = session.supervisor.after_source_step
            def after_source_step(*args, **kwargs):
                result = ordinary_handoff(*args, **kwargs)
                if result["status"] == "settled" and not entered.is_set():
                    # This is the existing coordinator callback port. Keep the
                    # real source thread alive after its real public handoff so
                    # its ordinary finally-Stop occurs after owner finalization.
                    entered.set()
                    if not release.wait(8):
                        raise OSError("authored_source_finally_unreleased")
                return result
            session.supervisor.after_source_step = after_source_step
        control = session.start_control()
        port = urlsplit(control["upstream"]).port
        def request(command):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=12)
            try:
                connection.request("POST", "/control", json.dumps(command), headers={
                    "Content-Type":"application/json", "X-Home-Den-Internal":control["server_nonce"]})
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()
        stop = {"id":"first-successful-stop", "operation":"stop"}
        try:
            self.assertEqual(request({"id":"owner-start", "operation":"start"})[0], 200)
            deadline = time.monotonic() + 12
            if delayed_source_finally:
                self.assertTrue(entered.wait(12), "real public source/worker handoff did not settle")
            else:
                while time.monotonic() < deadline and not session.source_handoffs:
                    time.sleep(.05)
                self.assertTrue(session.source_handoffs, session.status())
                self.assertEqual(session.source_handoffs[0]["status"], "settled")
            code, first = request(stop)
            self.assertEqual(code, 200)
            self.assertEqual(first["status"], "stopped", first)
            self.assertEqual(first["drain"]["status"], "complete")
            self.assertEqual(first["drain"]["worker_status"], "settled")
            self.assertTrue(session.supervisor.graph.finished)
            original_archive = session.archive_file.read_bytes()
            if delayed_source_finally:
                self.assertTrue(session.supervisor.thread.is_alive())
                release.set()
                session.supervisor.thread.join(3)
                self.assertFalse(session.supervisor.thread.is_alive())
                self.assertEqual(request({"id":"after-source-finally", "operation":"status"}), (200, first),
                    "the ordinary source finally-Stop changed a successfully finalized result")
            self.assertEqual(request({"id":"new-successful-stop", "operation":"stop"}), (200, first),
                "a new owner Stop re-entered retirement after successful journal close")
            self.assertEqual(request(stop), (200, first))
            code, final = request({"id":"terminal-results", "operation":"read-results"})
            self.assertEqual(code, 200)
            self.assertEqual(final["status"], "stopped", final)
            self.assertEqual(final["ordinary_replay"]["status"], "reproduced", final)
            self.assertEqual(session.archive_file.read_bytes(), original_archive)
            closed = session.close()
            self.assertEqual(closed["status"], "stopped", closed)
            self.assertEqual(closed["ordinary_replay"]["status"], "reproduced", closed)
            self.assertEqual(session.archive_file.read_bytes(), original_archive)
        finally:
            release.set()
            session.close()

    def test_successful_owner_stop_and_later_new_ids_keep_original_terminal_result(self):
        self.successful_stop_case(delayed_source_finally=False)

    def test_source_finally_stop_after_owner_finalization_keeps_original_terminal_result(self):
        self.successful_stop_case(delayed_source_finally=True)

    def test_owner_reads_and_identity_checks_do_not_wait_for_retirement_io(self):
        directory = Path(__file__).resolve().parents[2] / "artifacts" / ("controlled-manual-stop-" + uuid.uuid4().hex)
        directory.mkdir(parents=True)
        print("authored manual Stop evidence:", directory, flush=True)
        bundle = bundle_for_manual()
        bundle["external_io"].set_scenario("perception_unavailable")
        session = ControlledSession(bundle=bundle, output_directory=directory, node_runtime=shutil.which("node"),
            source_bundle_digest="c" * 64,
            session_limits={"max_runtime_s":120,"max_cycles":24,"interval_s":1.5,"drain_timeout_s":1})
        entered, release = threading.Event(), threading.Event()
        ordinary_factory = session.supervisor.recorder_factory
        def recorder_factory(graph):
            recorder = ordinary_factory(graph)
            ordinary_record = recorder.record
            def record(original, **kwargs):
                retirement = original["kind"] == "journal_prepare" and any(
                    row["operation"]["operation"] == "retire"
                    for row in original["data"].get("control_operations", []))
                if retirement:
                    # Inject only the recorder's public I/O port. The actual SQL
                    # transaction, worker, source and recorder remain unchanged.
                    with recorder.lock:
                        entered.set()
                        if not release.wait(6):
                            raise OSError("authored_retirement_io_unreleased")
                        return ordinary_record(original, **kwargs)
                return ordinary_record(original, **kwargs)
            recorder.record = record
            return recorder
        session.supervisor.recorder_factory = recorder_factory
        control = session.start_control()
        port = urlsplit(control["upstream"]).port
        def request(command):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=4)
            try:
                connection.request("POST", "/control", json.dumps(command), headers={
                    "Content-Type":"application/json", "X-Home-Den-Internal":control["server_nonce"]})
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()
        def current_decision():
            address = session.supervisor.graph.address
            connection = http.client.HTTPConnection(*address, timeout=2)
            try:
                connection.request("GET", "/api/home-context/v1/snapshot?site_id=authored-site&room_id=authored-room",
                    headers={"Authorization":"Bearer " + session.human_token})
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                return next((row["payload"] for row in json.loads(response.read())["items"]
                    if row["payload_type"] == "home-live-shadow-decision-10"), None)
            finally:
                connection.close()
        calls, elapsed, threads = {}, {}, []
        def concurrent(name, command):
            def run():
                began = time.monotonic()
                try:
                    calls[name] = request(command)
                except Exception as error:
                    calls[name] = error
                finally:
                    elapsed[name] = time.monotonic() - began
            thread = threading.Thread(target=run, name="authored-owner-" + name, daemon=True)
            threads.append(thread)
            thread.start()
            return thread
        stop = {"id":"owner-manual-stop", "operation":"stop"}
        first = None
        try:
            code, started = request({"id":"owner-manual-start", "operation":"start"})
            self.assertEqual(code, 200, started)
            deadline, decision = time.monotonic() + 12, None
            while time.monotonic() < deadline:
                status = session.status()
                if status["status"] == "running" and status["cycles"] >= 1:
                    decision = current_decision()
                    if decision is not None:
                        break
                time.sleep(.05)
            self.assertIsNotNone(decision, session.status())
            self.assertTrue(decision["room_control"]["control_active"])
            stopper = concurrent("stop", stop)
            self.assertTrue(entered.wait(2), "real retirement never reached its recorder preparation")
            retry = concurrent("retry", deepcopy(stop))
            mutation = concurrent("scenario", {"id":"queued-scenario", "operation":"scenario", "scenario":"presence"})
            readers = [concurrent("status", {"id":"during-stop-status", "operation":"status"}),
                concurrent("results", {"id":"during-stop-results", "operation":"read-results"}),
                concurrent("conflict", {"id":stop["id"], "operation":"status"})]
            for reader in readers:
                reader.join(.2)
            self.assertFalse(any(reader.is_alive() for reader in readers),
                "owner reads/identity checks waited behind Stop's mutation lock")
            self.assertTrue(retry.is_alive(), "an exact pending mutation retry did not wait for its original result")
            self.assertTrue(mutation.is_alive(), "a new mutation escaped serialization with Stop")
            for name in ("status", "results"):
                self.assertIsInstance(calls[name], tuple, calls[name])
                code, value = calls[name]
                self.assertEqual(code, 200, value)
                self.assertEqual(value["status"], "stopping")
                self.assertLess(elapsed[name], .35)
                self.assertFalse(value["physical_authority"])
                self.assertFalse(value["acceptance_eligible"])
            self.assertEqual(calls["results"][1]["ordinary_replay"],
                {"status":"unavailable", "reason":"session_not_terminal"})
            self.assertEqual(calls["conflict"], (403, {"schema":"home-den-controlled-error-1",
                "reason":"controlled_command_identity_conflict"}))
            self.assertFalse(release.is_set())
            stopper.join(1.5)
            self.assertFalse(stopper.is_alive(), "bounded Stop did not return while retirement remained pending")
            retry.join(.5)
            mutation.join(.5)
            self.assertFalse(retry.is_alive())
            self.assertFalse(mutation.is_alive())
            self.assertIsInstance(calls["stop"], tuple, calls["stop"])
            self.assertEqual(calls["stop"][0], 200)
            first = deepcopy(calls["stop"][1])
            self.assertLess(elapsed["stop"], 1.5)
            self.assertEqual(calls["retry"], calls["stop"], "a pending exact retry did not retain the first Stop result")
            self.assertEqual(calls["scenario"], (403, {"schema":"home-den-controlled-error-1",
                "reason":"controlled_session_terminal"}))
            self.assertEqual(first["status"], "incomplete", first)
            self.assertEqual(first["reason"], "control_retirement_unconfirmed")
            self.assertEqual(first["drain"]["worker_status"], "unconfirmed")
            code, results = request({"id":"bounded-stop-results", "operation":"read-results"})
            self.assertEqual(code, 200)
            self.assertEqual(results["ordinary_replay"],
                {"status":"incomplete", "reason":"control_retirement_unconfirmed"})
            self.assertEqual(request(stop), (200, first))
            release.set()
            session.supervisor._control_retirement_thread.join(2)
            self.assertFalse(session.supervisor._control_retirement_thread.is_alive())
            self.assertEqual(request(stop), (200, first), "late storage completion upgraded the original Stop")
            code, later = request({"id":"another-stop", "operation":"stop"})
            self.assertEqual((code, later), (200, first))
        finally:
            release.set()
            for thread in threads:
                thread.join(3)
            retirement = session.supervisor._control_retirement_thread
            if retirement is not None:
                retirement.join(3)
            retirement_pending = retirement is not None and retirement.is_alive()
            # Only after real pending I/O has settled, release the actual owned
            # resources with an explicitly incomplete terminal disposition.
            if session.supervisor.thread is not None:
                session.supervisor.thread.join(3)
            graph = session.supervisor.graph
            if graph is not None and not graph.finished and not retirement_pending:
                if session.supervisor.worker is not None:
                    session.supervisor.worker.close()
                if session.supervisor.rpc is not None:
                    session.supervisor.rpc.close()
                withdrawal = graph.withdraw("authored_cleanup")
                receipt = withdrawal["barrier_receipt"]
                session.supervisor.recorder.drain({"status":"incomplete", "reason":"control_retirement_unconfirmed",
                    "terminal_cursor":(receipt or {}).get("cursor", 0), "worker_status":"unconfirmed", "withdrawal_receipt":receipt})
                graph.finalize(reason="authored_cleanup", drain_receipt={"schema":"home-acquisition-drain-1", "version":1,
                    "session_ref":deepcopy(session.identity), "configuration_digest":session.configuration_digest,
                    "withdrawal_receipt":receipt, "recorder_status":"incomplete", "worker_status":"unconfirmed",
                    "terminal_cursor":(receipt or {}).get("cursor", 0)})
            session.close()
            self.assertFalse(retirement_pending, "retirement cleanup remained unconfirmed after the authored I/O release")
        if first is not None:
            self.assertEqual(session.control(stop), first)
            originals = [json.loads(line)["record"] for line in session.archive_file.read_text(encoding="utf-8").splitlines()]
            retirements = [row for row in originals if row["kind"] == "journal_prepare" and any(
                operation["operation"]["operation"] == "retire" for operation in row["data"].get("control_operations", []))]
            self.assertEqual(len(retirements), 1)
            replay = subprocess.run([shutil.which("node"), str(Path(__file__).parent / "replay.cjs"), str(session.archive_file)],
                capture_output=True, text=True, timeout=12)
            self.assertEqual(replay.returncode, 2, replay.stdout)
            self.assertEqual(json.loads(replay.stdout)["reason"], "archive_session_incomplete")


if __name__ == "__main__":
    unittest.main()
