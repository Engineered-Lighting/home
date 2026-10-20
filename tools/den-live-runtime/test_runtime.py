"""Controlled real journal/worker/archive replay plus bounded lifecycle failures."""
from copy import deepcopy
import json
import asyncio
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[2]
for directory in (ROOT / "stack/services/home-context", ROOT / "tools/den-live-runtime"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
from archive import Recorder, RecorderRPC, ArchiveRefusal, canonical, digest
from supervisor import NodeWorkerDriver, SessionSupervisor, SessionControlEdge
from journal import Journal, PREFIX
from live_test_helpers import NOW, PROFILE, SCOPE, event, fact, publication_ref, LiveHttp
from live_worker_test_helpers import worker_fixture, worker_receipt_resolver, describe
from live_delivery_test_helpers import delivery_worker_fixture, delivery_geometry

NODE = shutil.which("node")


class ActualOrdinaryReplayTests(unittest.TestCase):
    fixture_factory = staticmethod(worker_fixture)

    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix="den-runtime-authored-")
        self.addCleanup(self.work.cleanup)
        self.now = NOW + .2
        self.service, self.grants, self.config = self.fixture_factory()
        sanitized = {k: v for k, v in self.config.items() if k not in {"token", "token_file", "service_url"}}
        self.manifest = {"schema": "home-live-session-manifest-1", "version": 1,
                         "session_ref": self.config["identity"], "configuration_digest": describe(sanitized)["config_digest"],
                         "source_bundle_digest": "b" * 64, "worker_config": sanitized, "worker_description": describe(sanitized)}
        self.archive = Path(self.work.name) / "authored-session.jsonl"
        self.recorder = Recorder(self.archive, self.manifest, max_records=1024, max_bytes=16 * 1024 * 1024, min_free_bytes=1)
        self.addCleanup(lambda: self.recorder.drain({"status": "incomplete", "reason": "test_cleanup"}))
        self.journal = Journal(Path(self.work.name) / "authored.sqlite", grants=self.grants, profile=PROFILE,
            clock=lambda: self.now, live_config=self.service, live_receipt_resolver=worker_receipt_resolver)
        self.addCleanup(self.journal.close)
        self.journal.attach_recorder(self.recorder.record)
        self.http = LiveHttp(self.journal)
        self.addCleanup(self.http.close)
        self.config["service_url"] = "http://127.0.0.1:" + str(self.http.server.server_port)
        self.rpc = RecorderRPC(self.recorder)
        self.driver = NodeWorkerDriver(node_runtime=NODE, config=self.config, recorder=self.rpc.start(), controlled_time=self.now)
        self.addCleanup(self.rpc.close)
        self.addCleanup(self.driver.close)
        self.head = None
        self.geometry_head = None

    def publish(self, seq):
        self.now = NOW + seq - 1 + .2
        payload = fact("authored-runtime-fact-" + str(seq), seq=seq)
        if self.head:
            payload["head"]["supersedes"] = publication_ref(self.head)
        status, receipt = self.http.request("POST", PREFIX + "/events", event(payload))
        self.assertEqual(status, 200, receipt)
        self.head = receipt
        if "delivery_policy" in self.config:
            geometry = delivery_geometry(seq)
            if self.geometry_head:
                geometry["head"]["supersedes"] = publication_ref(self.geometry_head)
            status, self.geometry_head = self.http.request("POST", PREFIX + "/events", event(geometry))
            self.assertEqual(status, 200, self.geometry_head)
        return receipt

    def complete(self, count=6):
        for seq in range(1, count + 1):
            receipt = self.publish(seq)
            self.driver.cycle(self.now)
        terminal = self.driver.drain(deadline_ms=1000, through_cursor=receipt["cursor"])
        self.assertEqual(terminal["status"], "settled", terminal)
        self.recorder.drain({"status": "complete", "reason": "controlled_session", "terminal_cursor": terminal["input_cursor"]})
        return terminal

    def replay(self, filename=None):
        result = subprocess.run([NODE, str(ROOT / "tools/den-live-runtime/replay.cjs"), str(filename or self.archive)],
                                cwd=ROOT, capture_output=True, text=True, timeout=15)
        return result.returncode, json.loads(result.stdout)

    def rewrite(self, mutate):
        records = [json.loads(line) for line in self.archive.read_text().splitlines()]
        mutate(records)
        prior = None
        for seq, value in enumerate(records):
            value.update(seq=seq, previous_digest=prior)
            value.pop("digest", None)
            value["digest"] = digest(value)
            prior = value["digest"]
        filename = Path(self.work.name) / "authored-modified.jsonl"
        filename.write_text("".join(canonical(value) + "\n" for value in records), encoding="utf-8")
        return filename

    def test_actual_public_journal_and_worker_reproduce_all_causal_outputs(self):
        terminal = self.complete()
        status, result = self.replay()
        self.assertEqual(status, 0, result)
        self.assertEqual(result["status"], "reproduced")
        self.assertEqual(result["ticks"], 6)
        self.assertEqual(result["commits"], 6)
        self.assertGreaterEqual(terminal["input_cursor"], self.head["cursor"])
        self.assertFalse(result["acceptance_eligible"])

    def test_owned_worker_stop_admission_does_not_wait_for_its_pipe_lock(self):
        self.assertTrue(self.driver.lock.acquire(timeout=1))
        try:
            started = time.monotonic()
            self.driver.stop_admission()
            self.assertLess(time.monotonic()-started, .25)
        finally:
            self.driver.lock.release()
        deadline = time.monotonic()+1
        while self.driver.process.poll() is None and time.monotonic()<deadline:
            time.sleep(.01)
        self.assertIsNotNone(self.driver.process.poll())

    def test_rehashed_forged_output_is_recomputed_and_refused(self):
        self.complete()
        def mutate(records):
            row = next(row for row in records if row["record"]["kind"] == "worker_prepared")
            row["record"]["data"]["prepared"]["decision"]["scene"]["reason"] = "forged-success"
        status, result = self.replay(self.rewrite(mutate))
        self.assertEqual(status, 2)
        self.assertEqual(result["reason"], "ordinary_replay_output_mismatch")

    def test_rehashed_missing_originals_and_unconfirmed_transaction_are_incomplete(self):
        self.complete()
        def remove_source(records):
            tx = next(row["record"]["data"]["tx_id"] for row in records if row["record"]["kind"] == "journal_prepare")
            records[:] = [row for row in records if row["record"]["data"].get("tx_id") != tx]
        self.assertEqual(self.replay(self.rewrite(remove_source))[1]["reason"], "archive_missing_original_publication")
        partial = Path(self.work.name) / "authored-truncated.jsonl"
        partial.write_bytes(self.archive.read_bytes()[:-2])
        self.assertEqual(self.replay(partial)[1]["reason"], "archive_incomplete_tail")

    def test_recorder_failure_stops_new_admission_and_never_claims_complete(self):
        self.recorder.max_records = 2  # Header + durable prepare; commit marker fails after actual DB commit.
        payload = fact("authored-budget-fact", seq=1)
        status, response = self.http.request("POST", PREFIX + "/events", event(payload))
        self.assertEqual(status, 503, response)
        self.assertEqual(self.recorder.status()["status"], "incomplete")
        status, response = self.http.request("POST", PREFIX + "/events", event(fact("authored-after-loss", seq=2)))
        self.assertEqual(status, 503, response)
        self.assertEqual(self.recorder.drain({"status": "incomplete"})["status"], "incomplete")


class ActualDeliveryReplayTests(ActualOrdinaryReplayTests):
    fixture_factory = staticmethod(delivery_worker_fixture)

    def test_actual_public_journal_and_worker_reproduce_all_causal_outputs(self):
        self.complete(count=22)
        status, result = self.replay()
        self.assertEqual(status, 0, result)
        self.assertEqual(result["ticks"], 22)
        rows = [json.loads(line)["record"] for line in self.archive.read_text().splitlines()]
        decisions = [row["data"]["prepared"]["decision"] for row in rows if row["kind"] == "worker_prepared"]
        self.assertTrue(any(row["execution"].get("status") == "virtual" and row["execution"]["readback"][0]["brightness_fraction"] > 0 for row in decisions))
        self.assertFalse(result["acceptance_eligible"])


class RecorderAndSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix="den-supervisor-authored-")
        self.addCleanup(self.work.cleanup)
        self.manifest = {"schema": "home-live-session-manifest-1", "version": 1}

    def recorder(self):
        return Recorder(Path(self.work.name) / "session.jsonl", self.manifest, max_records=32, max_bytes=65536, min_free_bytes=1)

    def test_exact_record_retry_and_secret_media_refusal(self):
        recorder = self.recorder()
        self.addCleanup(lambda: recorder.drain({"status": "incomplete"}))
        record = {"kind": "worker_cut", "data": {"cut": {"items": []}, "received_at": 1}}
        receipt = recorder.record(record, record_id="authored-request-1")
        self.assertEqual(recorder.record(record, record_id="authored-request-1"), receipt)
        with self.assertRaisesRegex(ArchiveRefusal, "identity_conflict"):
            recorder.record({"kind": "worker_cut", "data": {}}, record_id="authored-request-1")
        for key in ("token", "image_base64", "raw_depth"):
            with self.assertRaisesRegex(ArchiveRefusal, "secret_or_media"):
                recorder.record({"kind": "worker_cut", "data": {key: "never-retained"}})

    def supervisor(self, *, worker_settled=True, preflight=True):
        trace = []
        recorder = self.recorder()
        self.addCleanup(lambda: recorder.drain({"status": "incomplete"}))
        config = {"schema": "home-live-session-config-1", "version": 1, "session_ref": deepcopy(SCOPE),
                  "configuration_digest": "a" * 64, "mode": "controlled", "max_runtime_s": 1,
                  "max_cycles": 1, "interval_s": .5, "drain_timeout_s": .5}
        class Graph:
            configuration_digest = config["configuration_digest"]
            journal = SimpleNamespace(attach_recorder=lambda callback: trace.append("recorder_attached"))
            def readiness(self):
                return {"session_ref": config["session_ref"], "configuration_digest": config["configuration_digest"], "inflight": False}
            def step(self):
                trace.append("source_step")
                return {"status": "completed"}
            def quiesce(self, reason):
                trace.append("quiesce")
                return {"status": "quiescent"}
            def withdraw(self, reason):
                trace.append("withdraw")
                return {"withdrawal": "recorded", "barrier_receipt": {"cursor": 7}}
            def finalize(self, *, reason, drain_receipt):
                trace.append("finalize")
                self.drain = drain_receipt
                assert recorder.closed, "finalize must follow recorder drain"
                return {"status": "stopped" if drain_receipt["recorder_status"] == "drained" else "unconfirmed"}
        class Worker:
            def readiness(self):
                trace.append("worker_ready")
                return {"status": "ready"}
            def drain(self, **kwargs):
                trace.append("worker_drain")
                return {"status": "settled" if worker_settled else "unconfirmed", "input_cursor": kwargs["through_cursor"]}
            def close(self):
                trace.append("worker_close")
            def stop_admission(self):
                trace.append("worker_stop_admission")
        graph = Graph()
        supervisor = SessionSupervisor(config, graph_factory=lambda: graph, recorder_factory=lambda _: recorder,
            worker_factory=lambda *_: Worker(), preflight=lambda _: {"status": "ready" if preflight else "refused"})
        return supervisor, graph, trace

    def test_one_start_orders_readiness_then_withdraw_worker_recorder_and_finalize(self):
        supervisor, graph, trace = self.supervisor()
        supervisor.start()
        deadline = time.monotonic() + 3
        while supervisor.state not in {"stopped", "incomplete"} and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(supervisor.state, "stopped", supervisor.status())
        self.assertLess(trace.index("recorder_attached"), trace.index("worker_ready"))
        self.assertLess(trace.index("worker_ready"), trace.index("source_step"))
        self.assertLess(trace.index("withdraw"), trace.index("worker_drain"))
        self.assertLess(trace.index("worker_drain"), trace.index("finalize"))
        self.assertEqual(graph.drain["terminal_cursor"], 7)
        self.assertEqual(graph.drain["recorder_status"], "drained")

    def test_idle_stop_is_terminal_and_repeated_stop_does_not_change_it(self):
        supervisor, graph, trace = self.supervisor()
        first = supervisor.stop()
        self.assertEqual(first['status'], 'stopped')
        self.assertEqual(supervisor.stop(), first)
        self.assertEqual(supervisor.status(), first)
        self.assertEqual(trace, [])

    def test_early_stop_does_not_skip_cleanup_of_inflight_graph_construction(self):
        import threading
        supervisor, graph, trace = self.supervisor()
        entered, release = threading.Event(), threading.Event()
        def graph_factory():
            entered.set()
            release.wait(3)
            return graph
        supervisor.graph_factory = graph_factory
        supervisor.start()
        try:
            self.assertTrue(entered.wait(1))
            early = supervisor.stop()
            self.assertEqual(early['status'], 'incomplete')
            self.assertNotIn('quiesce', trace)
            release.set()
            supervisor.thread.join(2)
            self.assertFalse(supervisor.thread.is_alive())
            self.assertIn('quiesce', trace, 'an interim no-graph Stop suppressed later real graph cleanup')
            self.assertIn('withdraw', trace)
            self.assertIn('finalize', trace)
        finally:
            release.set()
            supervisor.thread.join(2)

    def test_manual_authority_activates_after_recording_and_retires_before_source_withdrawal(self):
        supervisor, graph, trace = self.supervisor()
        class Control:
            active = False
            def state(self):
                return {'control_generation':int(self.active),'control_active':self.active}
            def activate(self, operation_id):
                trace.append('control_activate')
                self.active = True
                return {'id':operation_id}
            def retire(self, operation_id, reason):
                trace.append('control_retire')
                self.active = False
                return {'id':operation_id}
        graph.journal.dynamic = SimpleNamespace(worker=SimpleNamespace(control_authority=Control()))
        supervisor.start()
        supervisor.thread.join(3)
        self.assertEqual(supervisor.state, 'stopped', supervisor.status())
        self.assertLess(trace.index('recorder_attached'), trace.index('control_activate'))
        self.assertLess(trace.index('control_activate'), trace.index('worker_ready'))
        self.assertLess(trace.index('control_retire'), trace.index('withdraw'))
        self.assertLess(trace.index('control_retire'), trace.index('worker_drain'))

    def blocked_control_retirement(self, phase):
        import threading
        supervisor, graph, trace = self.supervisor()
        entered, release = threading.Event(), threading.Event()
        recorder = supervisor.recorder_factory(graph)
        class Control:
            active = False
            generation = 0
            def block(self):
                # Actual recorder lock models a callback still inside durable I/O.
                with recorder.lock:
                    entered.set()
                    release.wait(3)
            def state(self):
                trace.append('control_state')
                if phase == 'state': self.block()
                return {'control_generation':self.generation,'control_active':self.active}
            def activate(self, operation_id):
                self.active, self.generation = True, 1
            def retire(self, operation_id, reason):
                trace.append('control_retire')
                if phase == 'retire': self.block()
                self.active, self.generation = False, 2
        graph.journal.dynamic = SimpleNamespace(worker=SimpleNamespace(control_authority=Control()))
        supervisor.start()
        try:
            self.assertTrue(entered.wait(1))
            readback = []
            reader = threading.Thread(target=lambda:readback.append(supervisor.status()),daemon=True)
            reader.start();reader.join(.15)
            self.assertFalse(reader.is_alive(),'status waited for the pending recorder lock')
            started = time.monotonic()
            supervisor.thread.join(.75)
            self.assertFalse(supervisor.thread.is_alive(),'Stop exceeded its original .5s drain budget')
            self.assertLess(time.monotonic()-started,.75)
            first = supervisor.stop()
            self.assertEqual(first['status'],'incomplete')
            self.assertEqual(first['reason'],'control_retirement_unconfirmed')
            self.assertEqual(first['drain']['worker_status'],'unconfirmed')
            self.assertEqual(supervisor.read_results()['ordinary_replay']['reason'],'control_retirement_unconfirmed')
            self.assertLess(trace.index('quiesce'),trace.index('control_state'))
            self.assertEqual(trace.count('worker_stop_admission'),1)
            for prohibited in ('withdraw','worker_drain','finalize','worker_close'):
                self.assertNotIn(prohibited,trace)
            release.set()
            supervisor._control_retirement_thread.join(1)
            self.assertFalse(supervisor._control_retirement_thread.is_alive())
            self.assertEqual(supervisor.stop(),first,'late retirement cannot upgrade the first incomplete result')
            self.assertEqual(supervisor.status(),first)
        finally:
            release.set()
            supervisor.thread.join(2)
            reader = locals().get('reader')
            if reader: reader.join(1)

    def test_blocked_control_state_keeps_stop_and_results_bounded(self):
        self.blocked_control_retirement('state')

    def test_blocked_control_retire_keeps_stop_and_results_bounded(self):
        self.blocked_control_retirement('retire')

    def test_unconfirmed_worker_drain_remains_incomplete_and_preflight_never_starts_source(self):
        supervisor, graph, trace = self.supervisor(worker_settled=False)
        supervisor.start()
        deadline = time.monotonic() + 3
        while supervisor.state not in {"stopped", "incomplete"} and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(supervisor.state, "incomplete")
        self.assertEqual(graph.drain["worker_status"], "unconfirmed")
        self.assertEqual(graph.drain["recorder_status"], "incomplete")

    def test_preparation_is_stoppable_without_waiting_for_native_completion_or_starting_source(self):
        import threading
        supervisor, graph, trace = self.supervisor()
        gate, entered = threading.Event(), threading.Event()
        def preparation():
            entered.set()
            gate.wait(2)
            return graph
        supervisor.graph_factory = preparation
        supervisor.stop_preparation = lambda: (trace.append("preparation_cancel"), gate.set())
        graph.finalize = lambda **_: {"status": "unconfirmed"}
        started = supervisor.start()
        self.assertIn(started["status"], {"starting", "incomplete"})
        self.assertTrue(entered.wait(1))
        supervisor.stop()
        supervisor.thread.join(2)
        self.assertFalse(supervisor.thread.is_alive())
        self.assertNotIn("source_step", trace)
        self.assertNotIn("worker_ready", trace)
        self.assertIn("preparation_cancel", trace)
        self.assertEqual(supervisor.state, "incomplete")

    def test_operator_token_does_not_inherit_context_or_lab_access_and_routes_are_closed(self):
        calls, lab_calls = [], []
        async def lab(scope, receive, send):
            lab_calls.append(scope["path"])
            await send({"type": "http.response.start", "status": 401, "headers": []})
            await send({"type": "http.response.body", "body": b'{}'})
        token = "fictional-dedicated-operator-token"
        supervisor = SimpleNamespace(control=lambda command: (calls.append(command), {"schema": "home-live-session-status-1", "status": "idle"})[1])
        edge = SessionControlEdge(lab, supervisor=supervisor, token_sha256=hashlib.sha256(token.encode()).hexdigest())
        async def invoke(path, credential, body):
            messages = []
            scope = {"type": "http", "method": "POST", "path": path, "query_string": b"", "headers": [
                (b"authorization", ("Bearer " + credential).encode()), (b"content-type", b"application/json")]}
            async def receive():
                return {"type": "http.request", "body": body, "more_body": False}
            async def send(value):
                messages.append(value)
            await edge(scope, receive, send)
            return messages[0]["status"]
        body = b'{"operation":"status"}'
        self.assertEqual(asyncio.run(invoke("/api/home-live-session/v1/status", "Lab-token", body)), 403)
        self.assertEqual(calls, [])
        self.assertEqual(asyncio.run(invoke("/api/home-live-session/v1/status", token, body)), 200)
        self.assertEqual(asyncio.run(invoke("/api/home-live-session/v1/status", token, b'{"operation":"status","operation":"start"}')), 403)
        self.assertEqual(asyncio.run(invoke("/api/perception-lab/v1/sessions", token, body)), 401)
        self.assertEqual(len(lab_calls), 1)

    def test_producer_only_development_uses_barrier_cursor_and_retains_private_assessment(self):
        supervisor, graph, trace = self.supervisor()
        supervisor.config["mode"] = "development"
        supervisor.worker_factory = lambda *_: None
        output = Path(self.work.name) / "private-candidates.json"
        supervisor.assessment_file = str(output)
        class Assessment:
            def export(self):
                return {"records": [{"id": "fictional-candidate"}], "complete": True}
            def write(self, filename):
                Path(filename).write_text('{"authored":true}')
        graph.assessment_recorder = Assessment()
        supervisor.start()
        supervisor.thread.join(3)
        self.assertEqual(supervisor.state, "stopped", supervisor.status())
        self.assertEqual(graph.drain["terminal_cursor"], 7)
        self.assertEqual(graph.drain["worker_status"], "settled")
        self.assertEqual(supervisor.status()["assessment"]["status"], "retained")
        self.assertTrue(output.is_file())
        self.assertEqual(supervisor.read_results()["ordinary_replay"]["status"], "not_applicable")

    def test_resident_preparation_closed_reason_survives_without_exception_text_leak(self):
        supervisor, graph, trace = self.supervisor()
        class MissingModel(ValueError):
            reason = "resident_models_not_provisioned"
        def missing():
            raise MissingModel("sensitive owner path must not escape")
        supervisor.graph_factory = missing
        supervisor.start()
        supervisor.thread.join(3)
        self.assertEqual(supervisor.status()["reason"], "resident_models_not_provisioned")
        self.assertNotIn("sensitive", json.dumps(supervisor.status()))
        self.assertNotIn("source_step", trace)


if __name__ == "__main__":
    unittest.main()
