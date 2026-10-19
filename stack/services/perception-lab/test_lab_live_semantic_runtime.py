"""Authored incumbent Store -> semantic graph -> actual Journal; loopback only.

No camera/model/host I/O, qualified household geometry or authoritative rows
are injected. External HA and JEV provider inputs are authored and labelled.
"""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
for directory in (ROOT / "stack/services/home-context", ROOT / "tools/den-live-runtime"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import live_test_helpers
from journal import digest
from live_capture import _original_digest
from live_test_helpers import METHOD, NOW, TOKEN
from live_worker_test_helpers import WorkerProbe
from test_live_pc_support import authored_pc_fixture
import test_lab_live_runtime as historical_runtime
from test_lab_live_semantic_admission_v2 import semantic_v2_fixture
from pc_support_configuration import upgrade_configuration
from controlled_external_io import assemble_journal_grants
from live_reasoning import LiveReasoning
from reasoning import ACTIVITIES


class LabLiveSemanticRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.base = historical_runtime.LabLiveRuntimeTests()
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.f = self.base.f
        scope = deepcopy(self.f.config["manifest"]["identity"])
        scope_patch = mock.patch.dict(live_test_helpers.SCOPE, scope, clear=True)
        scope_patch.start()
        self.addCleanup(scope_patch.stop)
        config, policy, _, worker = semantic_v2_fixture(include_worker=True)
        camera = config["manifest"]["sources"]["synthetic-source"]
        if METHOD not in camera["association_method_refs"]:
            camera["association_method_refs"].append(deepcopy(METHOD))
        camera["model_refs"] = [deepcopy(self.f.model)]
        # Re-close the new explicit sibling after changing actual fixture model
        # pins. This is configuration preparation, never a qualified source.
        config.update(schema="home-live-ingress-config-9", version=9)
        config["manifest"].update(schema="home-live-ingress-manifest-9", version=9)
        config["manifest"].pop("pc_support_interface_digest", None)
        for key in ("pc_support_policy", "pc_support_schema_pins"):
            config["manifest"]["worker"].pop(key, None)
        config, worker = upgrade_configuration(config, worker, worker["pc_support_policy"])
        policy["pc_support_policy_digest"] = digest(worker["pc_support_policy"])
        self.worker_token = "authored-semantic-runtime-worker-token"
        grants = assemble_journal_grants(config, worker, publication_token=TOKEN,
                                        worker_token=self.worker_token, human_token="authored-semantic-human")
        for grant in grants:
            grant["revisions"] = list(dict.fromkeys([*grant["revisions"], "synthetic-r1", worker["revision"]]))
        self.producer = self.base.producer()
        self.producer.update(schema="home-incumbent-lab-producer-2", version=2,
                             live_config=config, semantic_policy=policy, journal_grants=grants)
        self.producer["limits"].update(max_cycles=8)
        self.producer["original_archive"]["max_records"] = 16
        self.worker_config = worker
        self.f.source = camera
        self.f.now = NOW + 1.8
        self.graph = None
        self.prepares = []

    def add_chain(self):
        camera = self.f.add_original(observed=NOW + 1., mutation=lambda row: row["payload"].update(arm="b"))
        ha = authored_pc_fixture(snapshot_at=NOW + 1.2, input_at=NOW + .9)["original"]["ha_event"]
        hid = self.f.store.append_event(self.f.session["id"], "ha.observation", ha["payload"])
        ha = next(row for row in self.f.store.list_events(self.f.session["id"]) if row["id"] == hid)
        packet = {"model": "jev-1.13.0", "state": {"schema": "den-facts-7", "now": {
            "people_in_view": "one", "camera_evidence": "current"}, "devices": {
            "pc_input": "under_15s", "pc_active": "on"}}, "questions": {"activity": {"instructions": "activity?"}}}
        evidence = [str(camera["id"]), str(hid)]
        timeline = [str(camera["id"])]
        provenance = {"request_id": "authored-semantic-runtime-jev", "session_id": self.f.session["id"],
            "site_id": "victoria", "arm": "b", "generation": 1, "observed_at": NOW + 1.4,
            "deadline": NOW + 15, "wire_version": "den-facts-7", "response_version": "jev-1.13.0",
            "input_digest": digest(packet), "evidence_ids": evidence, "timeline_ids": timeline,
            "evidence_digest": digest([camera["payload"], ha["payload"]])}
        request = {"request_id": provenance["request_id"], "arm": "b", "generation": 1,
            "available_at": NOW + 1.45, "packet_sha256": digest(packet), "request": packet,
            "status": "admitted_before_transport", "wire_version": "den-facts-7", "evidence_ids": evidence,
            "timeline_ids": timeline, "evidence_digest": provenance["evidence_digest"], "timeline_verified": "full"}
        self.f.store.append_event(self.f.session["id"], "reasoning.request", request)
        self.f.store.append_event(self.f.session["id"], "reasoning.delivery", {
            "request_id": provenance["request_id"], "arm": "b", "generation": 1, "available_at": NOW + 1.6,
            "state": "completed", "status": "ready", "current": True})
        runner = LiveReasoning.__new__(LiveReasoning)
        runner.session, runner.sid, runner.policy = self.f.session, self.f.session["id"], {}
        runner.store = self.f.store
        provider = {"status": "ready", "judgment": {"activity": "computer_work", "confidence": .8,
            "probabilities": {k: 1. if k == "computer_work" else 0. for k in ACTIVITIES}, "model_version": "jev-1.13.0"},
            "diagnostics": {"request": packet}, "provenance": provenance}
        out = runner._output({"request_id": provenance["request_id"], "site_id": "victoria", "arm": "b",
            "generation": 1, "cutoff": provenance["observed_at"], "deadline": provenance["deadline"],
            "records": [{**camera["payload"], "source_kind": "perception.output", "objects": [{"label": "person"}]}]},
            provider, NOW + 1.6)
        oid = self.f.store.append_event(self.f.session["id"], "reasoning.output", out)
        return camera, ha, oid

    def record(self, data, **kwargs):
        if data["kind"] == "journal_prepare":
            retained = {item[0] for item in self.base.composition.originals.records.values()}
            for cached in self.graph.semantic.prepared.values():
                self.assertIn(_original_digest(cached["retention_record"], limit=2 * 1024**2), retained)
            self.prepares.append(deepcopy(data))
        return self.f.record(data, **kwargs)

    def create_graph(self):
        composition = self.base.compose(self.producer)
        # Both commissioned clock surfaces are controlled together. Holding
        # wall time while real monotonic advances would correctly fail the
        # existing service-clock divergence guard during fixture preparation.
        composition.monotonic = lambda: self.f.now - NOW
        self.graph = self.base.create_graph(composition)
        self.graph.journal.attach_recorder(self.record)
        return self.graph

    def publications(self):
        return [json.loads(row[0]) for row in self.graph.journal.db.execute("SELECT body FROM publications ORDER BY cursor")]

    def test_actual_store_ha_and_reasoning_chain_retain_before_same_journal_admission(self):
        camera, ha, output_id = self.add_chain()
        graph = self.create_graph()
        self.assertEqual(graph.config["schema"], "home-incumbent-lab-acquisition-2")
        self.assertEqual(graph.step()["status"], "admitted_unqualified")
        self.assertEqual(graph.step()["status"], "admitted_unattributed")
        self.assertEqual(graph.step()["status"], "admitted_unattributed")
        rows = self.publications()
        # The actual LA adapter emits capture fact + unobservable geometry;
        # semantic HA and JEV add two facts, never supplied anatomy/work.
        self.assertEqual(len(rows), 4)
        geometries = [p["payload"] for p in rows if p["payload_type"] == "home-live-geometry-1"]
        self.assertEqual(len(geometries), 1)
        self.assertEqual(geometries[0]["status"], "unobservable")
        self.assertIsNone(geometries[0]["person_ref"])
        self.assertFalse(any(part.get("status") == "supplied" for part in geometries[0]["parts"].values()))
        self.assertEqual({p["payload"]["source_id"] for p in rows}, {"synthetic-source", "ha-semantic", "jev-semantic"})
        facts = {p["payload"]["source_id"]: p["payload"] for p in rows}
        self.assertEqual(facts["ha-semantic"]["value"], "active_unattributed")
        self.assertEqual(facts["jev-semantic"]["value"], "computer_work")
        self.assertIsNone(facts["ha-semantic"]["person_ref"])
        self.assertIsNone(facts["jev-semantic"]["person_ref"])
        self.assertEqual(graph.after_id, output_id)
        self.assertGreaterEqual(len(self.prepares), 3)
        self.assertEqual(len(self.base.composition.originals.records), 3)
        for fact in facts.values():
            ref = fact["original_receipt_refs"][0]
            self.assertEqual(graph._resolve_original(ref["store_id"], ref["receipt_id"])["ref"], ref)
        # The existing real Node client consumes the same Journal HTTP input
        # cut and its commit is independently accepted. Unattributed semantics
        # must still yield no supported person/work relationship.
        probe = WorkerProbe()
        self.addCleanup(probe.close)
        worker = {**deepcopy(self.worker_config), "token": self.worker_token,
                  "service_url": "http://127.0.0.1:" + str(graph.address[1])}
        probe.command("start", config=worker, t=self.f.now)
        consumed = probe.command("tick", t=self.f.now)
        self.assertEqual(len(consumed["decisions"]), 1, consumed)
        self.assertFalse(consumed["http_errors"], consumed)
        saved = graph.journal.db.execute("SELECT checkpoint FROM workers WHERE worker_id=?", (worker["worker_id"],)).fetchone()
        self.assertIsNotNone(saved)
        checkpoint = json.loads(saved[0])
        self.assertEqual(checkpoint["schema"], "home-live-worker-checkpoint-11")
        self.assertEqual(checkpoint["state"]["pc_input_originals"], [])
        self.assertFalse(checkpoint["state"]["engagement"]["reducer"]["state"]["relations"])
        stop = graph.withdraw()
        self.assertEqual(stop["withdrawal"], "recorded", stop)
        self.assertEqual(len(graph.source_barrier_acks), 3)
        self.assertEqual(graph.withdraw(), stop)
        sources = graph.journal.db.execute("SELECT source_id,generation FROM live_sources ORDER BY source_id").fetchall()
        self.assertEqual({r[0]: r[1] for r in sources}, {"synthetic-source": 2, "ha-semantic": 2, "jev-semantic": 2})
        self.assertEqual(self.f.store.get_session(self.f.session["id"])["state"], "recording")
        self.f.now += .01
        drained = probe.command("drain", t=self.f.now, through_cursor=stop["input_cursor"], deadline_ms=1000)
        self.assertEqual(drained["status"], "settled", drained)


    def test_expired_jev_does_not_wedge_later_current_ha(self):
        _, _, old_output = self.add_chain()
        self.f.now = NOW + 8
        current = authored_pc_fixture(snapshot_at=NOW + 7.8, input_at=NOW + 7.5)["original"]["ha_event"]
        current_id = self.f.store.append_event(self.f.session["id"], "ha.observation", current["payload"])
        graph = self.create_graph()
        self.assertEqual(graph.step()["status"], "expired")  # Historical HA after expired camera skip.
        expired = graph.step()
        self.assertEqual(expired["status"], "expired", expired)
        self.assertEqual(expired["after_id"], old_output)
        fresh = graph.step()
        self.assertEqual(fresh["status"], "admitted_unattributed", fresh)
        self.assertEqual(fresh["after_id"], current_id)
        facts = [p["payload"] for p in self.publications()]
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["source_id"], "ha-semantic")
        self.assertEqual(facts[0]["observed_at"], current["payload"]["observed_at"])
        self.assertIsNone(facts[0]["person_ref"])

    def test_lost_semantic_ack_retries_exact_original_without_duplicate_or_refreshed_receipt(self):
        self.add_chain()
        graph = self.create_graph()
        self.assertEqual(graph.step()["status"], "admitted_unqualified")
        actual_append, lost, attempts = graph.journal.append_event, [False], []
        def append(envelope, principal):
            ack = actual_append(envelope, principal)
            if envelope["payload"].get("source_id") == "ha-semantic":
                attempts.append(deepcopy(envelope))
                if not lost[0]:
                    lost[0] = True
                    raise TimeoutError("authored_semantic_ack_lost")
            return ack
        with mock.patch.object(graph.journal, "append_event", side_effect=append):
            failed = graph.step()
            self.assertEqual(failed["status"], "failed", failed)
            before = deepcopy(graph.pending_semantic)
            retained = deepcopy(self.base.composition.originals.records)
            self.f.now += .1
            retried = graph.step()
            self.assertEqual(retried["status"], "admitted_unattributed", retried)
        self.assertEqual(attempts[0], attempts[1])
        self.assertEqual(self.base.composition.originals.records, retained)
        self.assertEqual(attempts[1]["payload"]["available_at"], before["events"][0]["payload"]["available_at"])
        self.assertEqual(sum(p["payload"]["source_id"] == "ha-semantic" for p in self.publications()), 1)
        self.assertIsNone(graph.pending_semantic)

    def test_lost_second_barrier_ack_preserves_exact_cas_and_withdraws_all_three_sources_once(self):
        self.add_chain()
        graph = self.create_graph()
        for _ in range(3):
            self.assertIn(graph.step()["status"], {"admitted_unqualified", "admitted_unattributed"})
        actual_append, lost, attempts = graph.journal.append_event, [False], []
        def append(envelope, principal):
            ack = actual_append(envelope, principal)
            p = envelope["payload"]
            if p.get("source_id") == "ha-semantic" and p.get("status") == "withdrawn":
                attempts.append(deepcopy(envelope))
                if not lost[0]:
                    lost[0] = True
                    raise TimeoutError("authored_second_barrier_ack_lost")
            return ack
        with mock.patch.object(graph.journal, "append_event", side_effect=append):
            first = graph.withdraw()
            self.assertEqual(first["status"], "unconfirmed", first)
            self.assertEqual(len(graph.source_barrier_acks), 1)
            self.f.now += .1
            final = graph.withdraw()
            self.assertEqual(final["withdrawal"], "recorded", final)
        self.assertEqual(attempts[0], attempts[1])
        self.assertEqual(len(graph.source_barrier_acks), 3)
        self.assertEqual(len(self.publications()), 7)
        sources = graph.journal.db.execute("SELECT source_id,generation FROM live_sources ORDER BY source_id").fetchall()
        self.assertEqual({r[0]: r[1] for r in sources}, {"synthetic-source": 2, "ha-semantic": 2, "jev-semantic": 2})
        self.assertEqual(final["input_cursor"], graph.source_barrier_acks[-1]["cursor"])

    def test_foreign_reasoning_generation_is_ignored_while_current_ha_is_admitted(self):
        self.add_chain()
        # Append an original foreign-generation provider row; no row mutation.
        original = next(row for row in self.f.store.list_events(self.f.session["id"]) if row["kind"] == "reasoning.output")
        foreign = deepcopy(original["payload"])
        foreign["generation"] = 2
        self.f.store.append_event(self.f.session["id"], "reasoning.output", foreign)
        graph = self.create_graph()
        for _ in range(3):
            self.assertIn(graph.step()["status"], {"admitted_unqualified", "admitted_unattributed"})
        self.assertEqual(graph.step()["status"], "idle")
        self.assertEqual(len(self.publications()), 4)
        self.assertEqual(len(self.base.composition.originals.records), 3)

    def test_recorder_loss_prevents_new_semantic_admission_and_advancement(self):
        self.add_chain()
        graph = self.create_graph()
        self.assertEqual(graph.step()["status"], "admitted_unqualified")
        before = graph.after_id
        def failed_recorder(_record, **_):
            raise OSError("authored_recorder_loss")
        # Swap only the external recorder fault port, never Journal authority.
        graph.journal._recorder = failed_recorder
        failed = graph.step()
        self.assertEqual(failed["status"], "failed", failed)
        self.assertEqual(graph.after_id, before)
        self.assertEqual(len(self.publications()), 2)
        with mock.patch.object(self.f.store, "list_events", side_effect=AssertionError("read_after_recorder_loss")):
            result = graph.step()
        self.assertEqual(result["reason"], "recorder_ready_before_lab_admission_required")


if __name__ == "__main__":
    unittest.main()
